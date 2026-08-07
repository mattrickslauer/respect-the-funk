#!/usr/bin/env python3
"""Render the Forager design to PDF — vision, engineering, architecture, scale, submission.

    python3 forager_vision.py        # -> forager-vision.pdf

Seven pages:

  1. The vision        what an agentic OS for a label is, and why this agent is first
  2. The frontier      the lead table, and why the plan being a table is the whole trick
  3. The store         three shapes of truth, and why collapsing them breaks both
  4. Change            nothing is purged; staleness cascades; the TikTok walkthrough
  5. Scale             five axes it scales along, and the three ceilings it actually has
  6. The database      what CockroachDB is doing here that another store would not
  7. The submission    requirement coverage, demo beats, verified vs unverified, risk

Generated rather than drawn, for the same reason infra/platform_architecture.py is:
the design is described in exactly one place. Everything this PDF says comes from the
data blocks below. Change the design by changing this file.

Source of record is docs/superpowers/specs/2026-08-07-forager-agent-design.md; this
renders it, and where the two disagree the spec wins and this file is the bug.

Requires weasyprint. No graphviz — diagrams are hand-authored SVG.
"""

from pathlib import Path

from weasyprint import HTML

OUT = Path(__file__).parent / "forager-vision.pdf"
SPEC = "docs/superpowers/specs/2026-08-07-forager-agent-design.md"
SPEC_DATE = "2026-08-07"
DEADLINE = "2026-08-18 17:00 EDT"

TEAL, INK, SLATE = "#0f766e", "#1e293b", "#64748b"

# ───────────────────────────────────────────────────── page 1 — the vision

CONSUMERS = [
    ("Scout", "entity leads that resolved to a person with a reachable contact",
     "counterparty acquisition stops being a blocking decision"),
    ("Researcher", "the enrichment pattern itself — same frontier, different lead kinds",
     "may turn out to be the same agent"),
    ("Drafter", "facts, quotable chunks, and the artist's own voice",
     "outreach that cites something real"),
    ("Analyst", "metric time series and the audience hypotheses the Distiller wrote",
     "hypotheses that can be killed by evidence"),
    ("RemixKit", "artist identity, catalogue, brand voice",
     "assets that match who the artist actually is"),
]

WHY_FIRST = [
    ("Scout is gated; the Forager is not",
     "PLATFORM-SPEC §5 lists Scout first, and Scout needs SCOPE-RESET open decision 4 — "
     "counterparty acquisition — which is unresolved and marked the most urgent of the "
     "open decisions. The Forager reads sources we are entitled to read, so it can start "
     "today."),
    ("It makes the spine non-empty",
     "SCOPE-RESET §1 fixed the artist as the root because relationships, audience model "
     "and lessons compound between releases. All three have to come from somewhere. This "
     "is where they come from."),
    ("It is the same machine the Scout needs",
     "A lead that resolves to a person with a contact method is a counterparty candidate. "
     "Building this first means the Scout is a lead kind, not a second system."),
    ("It proves the memory claim a week early",
     "The frontier is the agent's plan, stored as rows. Kill it mid-crawl and restart: it "
     "resumes. That is PLATFORM-SPEC §8's closing demo beat, available on day 3."),
]

# ───────────────────────────────────────────────────── page 2 — the frontier

LEAD_KINDS = [
    ("profile", "resolve or refresh a platform profile", "auto"),
    ("catalogue", "releases and tracks from a metadata source", "auto"),
    ("metric", "recurring measurement — streams, views, followers, position", "auto · recurring"),
    ("engagement", "comments, mentions, tagged media on accounts we own", "auto"),
    ("mention_search", "public search for the artist on any platform, including absent ones", "auto"),
    ("fan_artifact", "a specific fan post, cover, edit or playlist", "auto"),
    ("document", "a web page to fetch and read", "auto"),
    ("entity", "a person or handle worth resolving — the Scout seam", "auto"),
    ("gap_query", "a model-proposed query for a dimension we are thin on", "auto"),
    ("— any of the above", "where no compliant API exists, the same lead goes to a human",
     "manual"),
]

FRONTIER_PROPS = [
    ("The plan is a table",
     "Not a context window, not a queue in another system. A recursive CTE up parent_lead_id "
     "answers <i>why were we ever looking there</i> — provenance of attention, which an agent "
     "that re-plans every run cannot answer at all."),
    ("One nullable column absorbs the scheduled crawler",
     "cadence_seconds NULL forages once and the lead dies. Set, and it reschedules itself "
     "forever. Stream counts and view counts become the same table, claim query and worker "
     "as discovery — not a second subsystem that has to agree with the first."),
    ("Dedup and leasing are constraints, not conventions",
     "UNIQUE (tenant_id, artist_id, target_hash) settles two Foragers discovering one handle "
     "in the same instant: one insert wins, the other retries and merges. And work is leased "
     "rather than locked, so a Forager that dies mid-fetch releases it by expiry, with no "
     "supervisor involved. PLATFORM-SPEC §3a and §3c, reused rather than re-derived."),
    ("Human work is inside the loop, not beside it",
     "Pillar 10 §5a: no compliant API returns who used a TikTok sound. Not a dead end — a "
     "lead with mode='manual', in the same queue, scored the same way, dispatched to a "
     "person. Their verdicts train the same scorer."),
]

# ───────────────────────────────────────────────────── page 3 — the store

STORE = [
    ("Evidence", "#7c3aed", "has quotes, no truth value", [
        ("artist_document", "platform, url, content_hash, raw_key, published_at, "
         "fetched_at, http_status, gone_at", "one row per thing fetched"),
        ("artist_chunk", "document_id, ordinal, text, embedding VECTOR(1024), token_count",
         "the RAG surface"),
    ]),
    ("Claims", TEAL, "has truth value and provenance, no prose", [
        ("artist_fact", "dimension, value_text, provenance, status, confidence, "
         "embedding VECTOR(1024), supersedes_id", "measured | inferred | asserted"),
        ("fact_basis", "fact_id, basis_kind, basis_id", "chunk | fact | profile | metric"),
    ]),
    ("Measurements", "#b45309", "append-only time series", [
        ("artist_metric", "platform, entity_kind, entity_id, metric, value, observed_at",
         "Tuesday does not supersede Monday"),
    ]),
    ("Configuration and judgment", "#1d4ed8", "what a human asserted", [
        ("artist_profile", "platform, mode, handle, credential_ref, enabled, confirmed_by",
         "mode: owned | unowned | absent"),
        ("artist_identifier", "kind, value, valid_from, valid_until", "aliases survive a rebrand"),
        ("verdict", "subject_kind, subject_id, relevant BOOL, judged_by", "the boolean button"),
        ("contradiction", "claim_a_id, claim_b_id, state, resolved_by",
         "asserted vs measured is never auto-resolved"),
    ]),
]

SPLIT_REASONS = [
    ("A paragraph is not a claim",
     "It is a thing someone wrote. Giving chunks a truth value they do not have is how a "
     "RAG system starts confidently reciting a 2019 blog post as current fact."),
    ("A claim cannot be quoted",
     "Facts are distilled. If they are the only layer, every answer is a paraphrase with "
     "no way back to the sentence it came from."),
    ("Time series are neither",
     "Monday's stream count is not superseded by Tuesday's — both are permanently true. "
     "Facts supersede; measurements accumulate. Forcing them into one shape loses one "
     "behaviour or the other."),
    ("The split is what makes change tractable",
     "Because evidence never goes stale and only claims do, a changed fact is a status "
     "transition instead of a delete cascade. See the next page."),
]

# ───────────────────────────────────────────────────── page 4 — change

TIKTOK_WALK = [
    ("Day 0", "A human asserts artist_profile(tiktok, absent).",
     "Absence is a positive assertion with an author and a timestamp — not a missing row, "
     "which would mean we never checked."),
    ("Day 0", "The Forager emits mention_search leads on TikTok anyway.",
     "An absent platform is still swept for fan activity. This is the point."),
    ("Day 0", "The Distiller writes an inferred fact: measurable TikTok fan activity, "
     "no artist presence — an unserved audience.",
     "fact_basis = {the profile row, the fan-artifact chunks}."),
    ("Day 40", "The artist makes a TikTok. Someone sets the profile to owned. The Reconciler "
     "walks fact_basis and marks the unserved-audience fact stale.",
     "New profile version appended; the old one superseded, not deleted. The walk is a "
     "changefeed-driven transitive closure over a dependency graph."),
    ("Day 40", "New leads seed — engagement, metric, profile refresh — and re-derivation "
     "writes a new live fact superseding the stale one.",
     "The existing mention_search leads keep running, now more useful because fan posts can "
     "be compared against owned posts. Documents and chunks untouched; not one row deleted."),
]

STATUSES = [
    ("live", "#dcfce7", "#166534", "current head of its chain — what retrieval reads"),
    ("superseded", "#e0e7ff", "#3730a3", "a newer version of this same claim exists"),
    ("stale", "#fef3c7", "#92400e", "basis changed or TTL expired — may still be true, "
     "no longer trusted, queued for re-derivation"),
    ("retracted", "#fee2e2", "#991b1b", "a human said it was wrong"),
]

CHANGE_CLASSES = [
    ("New account", "Profile transition, cascade through fact_basis, new leads seeded"),
    ("Rebrand", "artist_identifier keeps the old name as an alias with an end date — press "
     "written before the rebrand still uses it, and the drift gate must accept it"),
    ("New release", "A catalogue lead seeds per-track metric leads and reopens the dimensions "
     "that depend on catalogue"),
    ("Account lost or banned", "owned → unowned, the same cascade in reverse"),
    ("Human corrects a fact", "retracted, plus a new asserted fact. Asserted always beats inferred"),
    ("Source 404s on refetch", "Document marked gone_at; chunks retained — we still hold the "
     "text. Confidence decays because we can no longer re-verify. Not invalidated"),
]

# ───────────────────────────────────────────────────── page 5 — scale

SCALE_AXES = [
    ("Across the roster", "Rows, not code",
     "An artist with nine platforms and an artist with two run identical code, because "
     "mode and enabled are data on artist_profile. Adding artist #50 is an INSERT. Budget "
     "is per-artist, so one artist's crawl cannot starve the roster."),
    ("Across platforms", "One function",
     "A new platform is one adapter implementing fetch(lead, credentials) → documents, "
     "metrics, leads. The frontier, the store, scoring, budget, invalidation and retrieval "
     "are untouched. This is PLATFORM-SPEC §7's <i>a channel is data, not code</i>, applied "
     "to sources."),
    ("Across workers", "No orchestrator to become the bottleneck",
     "Workers are stateless; all state is rows. Throughput scales by starting more "
     "processes. FOR UPDATE SKIP LOCKED means N workers never collide and none of them "
     "needs to know the others exist."),
    ("Across time", "The cost curve bends down, not up",
     "This is the counter-intuitive one. Most crawlers get more expensive as they "
     "accumulate. Here target_hash makes re-crawls skip known targets, content_hash means "
     "an unchanged page is never re-embedded, dimension_policy means only volatile facts "
     "are rechecked, and verdicts make the scorer more precise. More history means less "
     "work per unit of value."),
    ("Across purposes", "The fleet shrinks instead of growing",
     "Scout and Researcher are both <i>expand a frontier, fetch, distil, write facts with "
     "provenance</i>. If this works, they are lead kinds rather than agents, and eight "
     "agents become six."),
]

CEILINGS = [
    ("Vector index write rate", "HIGH",
     "The real ceiling, and it is unmeasured. PLATFORM-SPEC §10 risk 4: large batch inserts "
     "degrade the index and IMPORT INTO is unsupported on tables carrying one. One artist "
     "crawl can produce thousands of chunks in a burst. Mitigation is chunk_staging — no "
     "vector index — drained by a paced mover driven by the same lead queue. Measure before "
     "trusting; delete the staging table if direct insert holds up."),
    ("Embedding cost", "MEDIUM",
     "Scales linearly with genuinely new text. Bounded by a per-document chunk cap and by "
     "skipping near-duplicate chunks by hash, but a wide crawl on a well-covered artist is "
     "still the largest variable cost in the system."),
    ("Serializable retries under contention", "MEDIUM",
     "Serializable is the reason to choose this database and also its cost. SKIP LOCKED "
     "keeps claim contention low, but retry loops are not optional and the application has "
     "to handle them."),
]

# ───────────────────────────────────────────────────── page 6 — the database

JOBS = [
    ("Memory", "Two vector surfaces plus the edges between them",
     "artist_chunk.embedding (evidence) and artist_fact.embedding (claims), with fact_basis "
     "joining them — all written in one transaction. <i>Find claims like X and show me the "
     "exact source sentences</i> is one query against one store. Split across Postgres and a "
     "vector service, that is two round trips with a staleness window between them."),
    ("State", "The plan is queryable",
     "fact_basis answers where a claim came from. A recursive CTE up parent_lead_id answers "
     "why we were ever looking there. Provenance of attention is a capability, not "
     "decoration — and it is only available because the frontier is relational."),
    ("Coordination", "A claim that becomes a test",
     "Two Foragers discover the same handle from different parents in the same instant. "
     "Under Read Committed: a duplicate row or a lost score update. Under serializable plus "
     "the unique index: one lead survives with merged provenance, the loser retries. "
     "PLATFORM-SPEC §1's claim becomes a test that fails on Postgres defaults."),
    ("Event bus", "The database as a build system",
     "The Reconciler is a changefeed-driven transitive closure over fact_basis — a row "
     "change propagating invalidation through a dependency graph. Flip one artist_profile "
     "row and watch staleness fan out while re-derivation leads appear, with zero deletes."),
]

GIVES_BACK = [
    ("Closes PLATFORM-SPEC §10 risk 2",
     "platform/README.md records that the RU cost of a filtered vector scan cannot be "
     "measured because <i>a probe with no rows measures nothing</i>. The Forager is the row "
     "generator — the first thing in the repository that makes that measurement possible."),
    ("Makes the Managed MCP Server demonstrable",
     "Natural-language queries over a two-table schema demo poorly. <i>What do we know about "
     "this artist's audience in Berlin, and where did we learn it?</i> over a real dossier "
     "with citations is a different thing entirely."),
    ("Moves the closing demo beat a week earlier",
     "The planned finale is killing the fleet mid-campaign on day 12. The Forager gives the "
     "identical beat on day 3 — kill it mid-crawl, restart, the frontier resumes — off the "
     "critical path."),
]

HONEST = [
    ("Changefeeds are an optimisation, not a dependency", "MITIGATED",
     "The Forager is the fleet's heaviest changefeed producer, pushing on the unverified RU "
     "draw. But the claim query already polls next_action_at, so the Forager runs correctly "
     "with changefeeds switched off entirely."),
    ("Ingest-only v1 would write vector indexes it never reads", "MITIGATED",
     "The first slice must include one retrieval query with EXPLAIN confirming prefix spans, "
     "or the headline claim goes unexercised for a week. An hour of work, not a milestone."),
    ("AS OF SYSTEM TIME is an operator tool, not a memory model", "SCOPED",
     "SELECT * FROM lead AS OF SYSTEM TIME '-4h' answers <i>what did the frontier look like "
     "before the drift</i> for free. The GC TTL on a Basic cluster bounds how far back it "
     "reaches, so it does not replace supersedes_id and is not presented as if it does."),
]

# ───────────────────────────────────────────────────── page 7 — the submission

COVERAGE = [
    ("Distributed vector indexing", "R1/R2 over counterparties — gated on open decision 4",
     "Also R-artist over chunks and facts, with citations. Unblocked today."),
    ("Cloud Managed MCP Server", "Queries over a two-table schema",
     "Queries over a real dossier that can cite its sources."),
    ("Memory integral to agents", "Threads resume after restart — day 12",
     "Frontier resumes after restart — day 3. Same claim, off the critical path."),
    ("ccloud CLI", "Not yet earned — the cluster was made in the console",
     "Unchanged. Use it for something real or drop the claim."),
]

DEMO = [
    ("Kill it mid-crawl and restart",
     "Every lead resumes from its row by lease expiry. The fleet is stateless; the database "
     "is the runtime."),
    ("Flip one profile from absent to owned",
     "Staleness fans out through fact_basis, re-derivation leads appear in the frontier, and "
     "nothing is deleted. The audit trail survives the change that invalidated it."),
    ("Ask why we were ever looking there",
     "A recursive CTE walks parent_lead_id back to the seed and prints the reason at every "
     "hop. No agent with a context-window plan can answer this."),
    ("Ask the dossier a question in English",
     "The Managed MCP Server over artist_fact and artist_chunk, answering with citations and "
     "with each claim's provenance class rendered distinguishably."),
    ("Show an audience nobody is serving",
     "An artist with measurable fan activity on a platform where they have no account. It "
     "falls out of the schema rather than being a special report."),
]

VERIFIED = [
    ("Vector indexes are enabled on Basic, and prefix filtering resolves to prefix spans",
     "VERIFIED",
     "platform/README.md items 1–2, against respect-the-funk-31317 (v26.2.5). Closed the "
     "flagged day-1 go/no-go, and EXPLAIN confirms the shape the retrieval design depends on."),
    ("Partial unique indexes and FOR UPDATE SKIP LOCKED both work", "VERIFIED",
     "platform/README.md items 3–4 — the two mechanisms the frontier is built out of, dedup "
     "and the lease."),
    ("RU cost of a filtered vector scan, and changefeed RU draw", "UNVERIFIED",
     "The first needs real row volume, which the Forager is what produces; the second needs a "
     "webhook sink to exist. Fallback to polling is already the claim mechanism."),
    ("Vector index degradation under burst writes", "UNVERIFIED",
     "Documented as a risk, not measured. chunk_staging exists to mitigate an unmeasured "
     "risk and should be deleted if measurement clears it."),
    ("Instagram needs no App Review for owned accounts", "VERIFIED",
     "research/01-platform-apis.md §1 — App Review and Business Verification gate Advanced "
     "Access only. Corrected an earlier assumption in this design that deferred Instagram."),
]

RISKS = [
    ("Twelve days, and the first slice is a migration plus five adapters", "HIGH",
     "Bounded deliberately — <b>ingest only, judged in SQL</b>: the migration, the frontier "
     "with lease and budget, five adapters, chunk-and-embed, one Distiller pass, one retrieval "
     "query with EXPLAIN. No console, no retrieval API, no verdict buttons. Everything else, "
     "including the Reconciler cascade and the human surface, is the second slice."),
    ("Topical drift — an artist name matches unrelated text", "HIGH",
     "The failure mode that actually kills a forager, and it is not volume. Identifier "
     "anchoring rejects anything past depth 2 reaching no canonical ID through no owned edge, "
     "and a cheap relevance check runs before the expensive fetch-and-embed."),
    ("Vector index write rate under burst", "HIGH",
     "The sharpest tension between this design and this database. chunk_staging plus a paced "
     "mover, and a measurement before the mover is trusted or deleted."),
    ("A model writes a fact that no source supports", "MEDIUM",
     "Every fact requires at least one fact_basis row, so a fact with no basis is rejected at "
     "write. Hallucination becomes a constraint violation, not a silent claim."),
    ("Two things we must not overclaim", "MEDIUM",
     "Counterparty acquisition (SCOPE-RESET open decision 4) has a mechanism in the manual "
     "lead mode, so it can be decided later — but it is not resolved and is not presented as "
     "resolved. And any curve that looks like an improvement metric gets labelled with its N: "
     "the house rule that made screen_clips.py abstain applies to our own demo."),
]


# ───────────────────────────────────────────────────────────────── diagrams

def svg_loop() -> str:
    """Page 1: the cycle — seed, frontier, fetch, three stores, distil, back to frontier."""
    return """
<svg viewBox="0 0 720 330" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="a1" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#0f766e"/></marker>
    <marker id="a2" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#b45309"/></marker>
  </defs>

  <rect x="10" y="40" width="112" height="46" rx="6" fill="#eff6ff" stroke="#1d4ed8"/>
  <text x="66" y="60" text-anchor="middle" font-size="11" font-weight="700"
        fill="#1e3a8a">seed</text>
  <text x="66" y="76" text-anchor="middle" font-size="8.5" fill="#4b6cb7">artist_profile</text>

  <path d="M122 63 L162 63" stroke="#0f766e" stroke-width="2" marker-end="url(#a1)"/>

  <rect x="164" y="26" width="150" height="74" rx="8" fill="#f0fdfa" stroke="#0f766e"
        stroke-width="2.5"/>
  <text x="239" y="50" text-anchor="middle" font-size="13" font-weight="700"
        fill="#134e4a">the frontier</text>
  <text x="239" y="67" text-anchor="middle" font-size="8.5" fill="#5b7c78">lead — the plan,</text>
  <text x="239" y="80" text-anchor="middle" font-size="8.5" fill="#5b7c78">as rows</text>
  <text x="239" y="93" text-anchor="middle" font-size="8" fill="#0f766e"
        font-family="monospace">claimed by lease</text>

  <path d="M314 63 L360 63" stroke="#0f766e" stroke-width="2" marker-end="url(#a1)"/>

  <rect x="362" y="34" width="120" height="58" rx="6" fill="#ffffff" stroke="#0f766e"/>
  <text x="422" y="56" text-anchor="middle" font-size="11" font-weight="700"
        fill="#134e4a">adapter</text>
  <text x="422" y="73" text-anchor="middle" font-size="8" fill="#5b7c78">fetch(lead) — one</text>
  <text x="422" y="84" text-anchor="middle" font-size="8" fill="#5b7c78">function per source</text>

  <path d="M482 63 L534 63" stroke="#0f766e" stroke-width="2" marker-end="url(#a1)"/>
  <text x="508" y="55" text-anchor="middle" font-size="7.5" fill="#0f766e">one txn</text>

  <rect x="536" y="18" width="174" height="30" rx="5" fill="#f5f3ff" stroke="#7c3aed"/>
  <text x="623" y="38" text-anchor="middle" font-size="10" font-weight="700"
        fill="#5b21b6">evidence — documents, chunks</text>
  <rect x="536" y="52" width="174" height="30" rx="5" fill="#f0fdfa" stroke="#0f766e"/>
  <text x="623" y="72" text-anchor="middle" font-size="10" font-weight="700"
        fill="#134e4a">claims — facts, fact_basis</text>
  <rect x="536" y="86" width="174" height="30" rx="5" fill="#fffbeb" stroke="#b45309"/>
  <text x="623" y="106" text-anchor="middle" font-size="10" font-weight="700"
        fill="#92400e">measurements — time series</text>

  <path d="M623 116 L623 158 L239 158 L239 172" stroke="#7c3aed" stroke-width="2"
        fill="none" stroke-dasharray="5 4"/>
  <text x="250" y="152" font-size="9" fill="#7c3aed">a chunk insert wakes the Distiller</text>

  <rect x="164" y="174" width="150" height="52" rx="8" fill="#f5f3ff" stroke="#7c3aed"
        stroke-width="2"/>
  <text x="239" y="196" text-anchor="middle" font-size="12" font-weight="700"
        fill="#5b21b6">Distiller</text>
  <text x="239" y="213" text-anchor="middle" font-size="8.5" fill="#7c3aed">chunks → facts + basis</text>

  <path d="M164 200 L96 200 L96 96" stroke="#0f766e" stroke-width="2" fill="none"
        marker-end="url(#a1)"/>
  <text x="86" y="150" text-anchor="end" font-size="9" fill="#0f766e">new leads</text>

  <rect x="362" y="174" width="150" height="52" rx="8" fill="#fef3c7" stroke="#b45309"
        stroke-width="2"/>
  <text x="437" y="196" text-anchor="middle" font-size="12" font-weight="700"
        fill="#92400e">a human</text>
  <text x="437" y="213" text-anchor="middle" font-size="8.5" fill="#b45309">relevant / not relevant</text>
  <path d="M362 200 L316 200" stroke="#b45309" stroke-width="2" marker-end="url(#a2)"/>

  <path d="M437 174 L437 134 L296 134 L296 102" stroke="#b45309" stroke-width="2"
        fill="none" stroke-dasharray="4 4" marker-end="url(#a2)"/>
  <text x="452" y="130" font-size="9" fill="#b45309">verdicts train the scorer</text>

  <rect x="10" y="252" width="700" height="62" rx="8" fill="#fafafa" stroke="#cbd5e1"/>
  <text x="24" y="272" font-size="10" font-weight="700" fill="#0f766e"
        letter-spacing="1.2">THE POINT</text>
  <text x="24" y="290" font-size="9.5" fill="#1e293b">The cycle closes. Fetching produces leads,
   so the graph expands on its own; the Distiller's facts reveal what is missing, so gaps become
   leads; and a</text>
  <text x="24" y="305" font-size="9.5" fill="#1e293b">human's verdict changes what gets fetched
   next. Nothing in this picture is an orchestrator — every arrow is a row being written.</text>
</svg>"""


def svg_lead_life() -> str:
    """Page 2: lead lifecycle, with the cadence fork."""
    return """
<svg viewBox="0 0 720 210" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="b1" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#0f766e"/></marker>
    <marker id="b2" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#b45309"/></marker>
  </defs>

  <rect x="12" y="60" width="104" height="34" rx="17" fill="#eff6ff" stroke="#1d4ed8"/>
  <text x="64" y="82" text-anchor="middle" font-size="11" font-weight="600" fill="#1e293b">pending</text>
  <path d="M116 77 L156 77" stroke="#0f766e" stroke-width="2" marker-end="url(#b1)"/>
  <text x="136" y="70" text-anchor="middle" font-size="7.5" fill="#0f766e">lease</text>

  <rect x="158" y="60" width="104" height="34" rx="17" fill="#eff6ff" stroke="#1d4ed8"/>
  <text x="210" y="82" text-anchor="middle" font-size="11" font-weight="600" fill="#1e293b">claimed</text>

  <path d="M262 77 L306 77" stroke="#0f766e" stroke-width="2" marker-end="url(#b1)"/>

  <rect x="308" y="60" width="104" height="34" rx="17" fill="#dcfce7" stroke="#166534"/>
  <text x="360" y="82" text-anchor="middle" font-size="11" font-weight="600" fill="#1e293b">done</text>

  <path d="M210 60 L210 26 L64 26 L64 58" stroke="#94a3b8" stroke-width="1.6" fill="none"
        stroke-dasharray="4 3" marker-end="url(#b1)"/>
  <text x="137" y="20" text-anchor="middle" font-size="8" fill="#64748b">lease expires — the worker died</text>

  <rect x="308" y="128" width="104" height="34" rx="17" fill="#fee2e2" stroke="#991b1b"/>
  <text x="360" y="150" text-anchor="middle" font-size="11" font-weight="600" fill="#1e293b">failed</text>
  <path d="M240 94 L240 145 L306 145" stroke="#991b1b" stroke-width="1.6" fill="none"
        marker-end="url(#b1)"/>
  <text x="246" y="135" font-size="8" fill="#991b1b">after n attempts</text>

  <path d="M360 94 L360 112 L64 112 L64 96" stroke="#b45309" stroke-width="2.2" fill="none"
        marker-end="url(#b2)"/>
  <text x="74" y="107" font-size="8.4" font-weight="600" fill="#b45309">
    reschedules if cadence_seconds set</text>

  <rect x="446" y="18" width="264" height="80" rx="8" fill="#fffbeb" stroke="#b45309"/>
  <text x="460" y="38" font-size="9.5" font-weight="700" fill="#92400e">ONE COLUMN, TWO LIFECYCLES</text>
  <text x="460" y="56" font-size="8.6" fill="#92400e">NULL — forage once. Fetch, distil, the lead dies.</text>
  <text x="460" y="70" font-size="8.6" fill="#92400e">Set — measure forever. Stream counts, view</text>
  <text x="460" y="83" font-size="8.6" fill="#92400e">counts, follower counts. Never dies.</text>

  <rect x="446" y="106" width="264" height="76" rx="8" fill="#f0fdfa" stroke="#0f766e"/>
  <text x="460" y="126" font-size="9.5" font-weight="700" fill="#134e4a">WHY THAT MATTERS</text>
  <text x="460" y="144" font-size="8.6" fill="#134e4a">Discovery and routine metric polling are not</text>
  <text x="460" y="157" font-size="8.6" fill="#134e4a">two subsystems that have to agree with each</text>
  <text x="460" y="170" font-size="8.6" fill="#134e4a">other. Same table, same claim, same worker.</text>

  <rect x="12" y="136" width="220" height="58" rx="8" fill="#f8fafc" stroke="#cbd5e1"/>
  <text x="26" y="155" font-size="9.5" font-weight="700" fill="#0f766e">ALSO TERMINAL</text>
  <text x="26" y="171" font-size="8.6" fill="#475569">rejected — scored below the floor</text>
  <text x="26" y="185" font-size="8.6" fill="#475569">blocked_manual — waiting on a person</text>
</svg>"""


def svg_cascade() -> str:
    """Page 4: the invalidation cascade, and what it does not touch."""
    return """
<svg viewBox="0 0 720 236" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="c1" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#b45309"/></marker>
    <marker id="c2" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"
            markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#0f766e"/></marker>
  </defs>

  <rect x="10" y="30" width="150" height="52" rx="7" fill="#fef3c7" stroke="#b45309"
        stroke-width="2"/>
  <text x="85" y="50" text-anchor="middle" font-size="10.5" font-weight="700"
        fill="#92400e">artist_profile</text>
  <text x="85" y="66" text-anchor="middle" font-size="8.5" fill="#b45309">absent → owned</text>
  <text x="85" y="77" text-anchor="middle" font-size="7.5" fill="#b45309">superseded, not deleted</text>

  <path d="M160 56 L206 56" stroke="#b45309" stroke-width="2" marker-end="url(#c1)"/>
  <text x="183" y="48" text-anchor="middle" font-size="7.5" fill="#b45309">changefeed</text>

  <rect x="208" y="30" width="128" height="52" rx="7" fill="#ffffff" stroke="#b45309"/>
  <text x="272" y="52" text-anchor="middle" font-size="10.5" font-weight="700"
        fill="#92400e">Reconciler</text>
  <text x="272" y="69" text-anchor="middle" font-size="8" fill="#b45309">walks fact_basis</text>

  <path d="M336 56 L382 56" stroke="#b45309" stroke-width="2" marker-end="url(#c1)"/>

  <rect x="384" y="18" width="150" height="30" rx="5" fill="#fef3c7" stroke="#b45309"/>
  <text x="459" y="38" text-anchor="middle" font-size="9.5" fill="#92400e">fact → stale</text>
  <rect x="384" y="52" width="150" height="30" rx="5" fill="#fef3c7" stroke="#b45309"/>
  <text x="459" y="72" text-anchor="middle" font-size="9.5" fill="#92400e">dependent fact → stale</text>

  <path d="M534 56 L578 56" stroke="#0f766e" stroke-width="2" marker-end="url(#c2)"/>

  <rect x="580" y="30" width="130" height="52" rx="7" fill="#f0fdfa" stroke="#0f766e"
        stroke-width="2"/>
  <text x="645" y="50" text-anchor="middle" font-size="10" font-weight="700"
        fill="#134e4a">re-derivation</text>
  <text x="645" y="66" text-anchor="middle" font-size="8" fill="#5b7c78">leads enqueued</text>
  <text x="645" y="77" text-anchor="middle" font-size="8" fill="#5b7c78">new live fact</text>

  <rect x="10" y="106" width="700" height="56" rx="8" fill="#f5f3ff" stroke="#7c3aed"
        stroke-width="2"/>
  <text x="26" y="126" font-size="10" font-weight="700" fill="#5b21b6"
        letter-spacing="1.2">UNTOUCHED BY ALL OF THE ABOVE</text>
  <text x="26" y="144" font-size="9.5" fill="#5b21b6">artist_document · artist_chunk — a 2026-06
   article saying "they're not on TikTok" is still a perfectly accurate quote of that article,</text>
  <text x="26" y="157" font-size="9.5" fill="#5b21b6">forever. Evidence never goes stale. Only the
   claims standing on it do. Not one row is deleted.</text>

  <rect x="10" y="172" width="700" height="56" rx="8" fill="#fafafa" stroke="#cbd5e1"/>
  <text x="26" y="192" font-size="10" font-weight="700" fill="#0f766e"
        letter-spacing="1.2">AND WHERE THE SYSTEM REFUSES TO DECIDE</text>
  <text x="26" y="210" font-size="9.5" fill="#1e293b">A TikTok search surfaces an account that looks
   like it <tspan font-style="italic">is</tspan> the artist — measured evidence contradicting an
   asserted absent. It raises a</text>
  <text x="26" y="223" font-size="9.5" fill="#1e293b">contradiction rather than flipping the config,
   because "the artist made a TikTok and didn't tell the label" is the finding.</text>
</svg>"""


# ─────────────────────────────────────────────────────────────────── helpers

def pill(text: str) -> str:
    return f'<span class="pill {text.split(",")[0].split(" ")[0]}">{text}</span>'


def pairs(data, width_pct: int, numbered: bool = False) -> str:
    """A two-column title/description table — denser than a stack of bordered blocks."""
    body = "".join(
        f"<tr><td class='b' style='width:{width_pct}%'>"
        f"{str(i + 1) + '. ' if numbered else ''}{t}</td><td>{d}</td></tr>"
        for i, (t, d) in enumerate(data))
    return f"<table><tbody>{body}</tbody></table>"


def rows(data, classes) -> str:
    out = []
    for row in data:
        cells = "".join(f"<td class='{c}'>{v}</td>" for v, c in zip(row, classes))
        out.append(f"<tr>{cells}</tr>")
    return "".join(out)


def build_html() -> str:
    store_blocks = ""
    for group, colour, gloss, tbls in STORE:
        body = "".join(
            f"<tr><td class='mono b'>{n}</td><td class='mono sm'>{c}</td>"
            f"<td class='note'>{note}</td></tr>" for n, c, note in tbls)
        store_blocks += (
            f"<h3 style='color:{colour}'>{group} <span class='note' "
            f"style='font-weight:400'>— {gloss}</span></h3>"
            f"<table><thead><tr><th style='width:19%'>Table</th>"
            f"<th style='width:50%'>Columns (abridged)</th><th>Note</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")

    why_first = pairs(WHY_FIRST, 26, numbered=True)
    props = pairs(FRONTIER_PROPS, 26)
    split = pairs(SPLIT_REASONS, 26)
    gives_back = pairs(GIVES_BACK, 26)

    statuses = "".join(
        f"<tr><td><span class='pill' style='background:{bg};color:{fg}'>{s}</span></td>"
        f"<td class='note'>{d}</td></tr>" for s, bg, fg, d in STATUSES)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 12mm 13mm 13mm 13mm;
  @bottom-center {{ content: "The Forager · {SPEC_DATE} · page " counter(page)
    " of " counter(pages); font: 8pt Helvetica; color: #94a3b8; }} }}
body {{ font: 8.5pt/1.30 Helvetica, Arial, sans-serif; color: {INK}; }}
h1 {{ font-size: 17pt; margin: 0 0 2pt; letter-spacing: -.4pt; }}
h2 {{ font-size: 11pt; margin: 10pt 0 4pt; padding-bottom: 2pt;
      border-bottom: 2px solid {TEAL}; color: {TEAL}; }}
h3 {{ font-size: 9.2pt; margin: 7pt 0 2pt; }}
.sub {{ color: {SLATE}; font-size: 8.6pt; margin: 0 0 2pt; }}
.meta {{ color: #94a3b8; font-size: 7.3pt; margin-bottom: 7pt; }}
.page {{ page-break-after: always; }}
.page:last-child {{ page-break-after: auto; }}
table {{ width: 100%; border-collapse: collapse; margin: 2pt 0 5pt; }}
th {{ text-align: left; font-size: 7pt; text-transform: uppercase; letter-spacing: .6pt;
     color: {SLATE}; border-bottom: 1.5px solid #cbd5e1; padding: 3pt 5pt 2pt; }}
td {{ padding: 2.4pt 5pt; border-bottom: 1px solid #e2e8f0; vertical-align: top;
      font-size: 7.8pt; }}
.mono {{ font-family: "DejaVu Sans Mono", monospace; }}
.sm {{ font-size: 7pt; }}
.b {{ font-weight: 700; }}
.note {{ color: {SLATE}; font-size: 7.4pt; }}
.thesis {{ background: #f0fdfa; border-left: 3px solid {TEAL}; padding: 5pt 9pt;
           margin: 6pt 0 7pt; font-size: 9pt; }}
.inv {{ border: 1px solid #cbd5e1; border-left: 3px solid {TEAL}; border-radius: 3px;
        padding: 6pt 10pt; margin: 5pt 0; break-inside: avoid; font-size: 8.4pt; }}
tr, svg, .code, .verdict, .thesis {{ break-inside: avoid; }}
h2, h3 {{ break-after: avoid; }}
.invh {{ font-weight: 700; margin-bottom: 2pt; font-size: 9pt; }}
.pill {{ font-size: 7.4pt; font-weight: 700; padding: 1.5pt 5pt; border-radius: 8pt; }}
.HIGH, .UNVERIFIED {{ background:#fee2e2; color:#991b1b; }}
.MEDIUM, .SCOPED {{ background:#fef3c7; color:#92400e; }}
.LOW, .VERIFIED, .MITIGATED {{ background:#dcfce7; color:#166534; }}
.code {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:3px; padding:6pt 9pt;
         font-family:"DejaVu Sans Mono",monospace; font-size:7.1pt; white-space:pre;
         margin:4pt 0; line-height:1.28; }}
.verdict {{ background:#fffbeb; border:1px solid #fbbf24; border-radius:4px;
            padding:7pt 11pt; margin:6pt 0; font-size:8.4pt; }}
</style></head><body>

<div class="page">
  <h1>The Forager</h1>
  <p class="sub">The first agent. It researches our own artists, and builds the context every
     other agent will read.</p>
  <p class="meta">Generated by {SPEC.rsplit('/', 1)[0]}/forager_vision.py · {SPEC_DATE} ·
     source of record {SPEC} · submission deadline {DEADLINE}</p>

  <div class="thesis"><b>An agentic OS for a record label is a fleet that is always running and
  always accumulating.</b> SCOPE-RESET §1 made the artist the spine because relationships, the
  audience model and lessons are what compound between releases. All three have to come from
  somewhere. This is the agent they come from — and it is the reason the spine is not empty.</div>

  {svg_loop()}

  <h2>Why this agent and not the Scout</h2>
  {why_first}

  <h2>Who reads what it writes</h2>
  <table><thead><tr><th style="width:13%">Agent</th><th style="width:44%">Reads</th>
  <th>What that buys</th></tr></thead><tbody>
  {rows(CONSUMERS, ['b', 'note', 'note'])}</tbody></table>

  <div class="verdict"><b>The product claim this agent has to earn.</b> A label's advantage is
  not that it knows how to market one song — it is that the fiftieth release starts from
  everything the first forty-nine taught it. That only happens if something writes it down,
  keeps track of where each piece came from, and notices when it stops being true. A person
  with a spreadsheet does the first, rarely the second, and never the third. This is the agent
  that does all three, continuously, for every artist on the roster at once.</div>
</div>

<div class="page">
  <h1>The frontier</h1>
  <p class="sub">The agent's plan is a table. Everything else follows from that one decision.</p>

  <div class="thesis">A <b>lead</b> is a typed candidate with a parent, a reason and a score.
  Adapters turn leads into evidence <i>and into more leads</i>. The graph expands on its own;
  scoring and budget bound it. A crawler can only find what someone already pointed it at — this
  finds the fan community nobody knew existed.</div>

  {svg_lead_life()}

  <h2>Five properties that come free</h2>
  {props}

  <h2>Lead kinds</h2>
  <table><thead><tr><th style="width:16%">Kind</th><th style="width:60%">What it is</th>
  <th>Mode</th></tr></thead><tbody>
  {rows(LEAD_KINDS, ['mono b', '', 'note'])}</tbody></table>

  <h2>Claiming work — PLATFORM-SPEC §3a, with thread → lead, verified on the real cluster</h2>
  <div class="code">UPDATE lead SET owner_agent = $agent, lease_expires_at = now() + INTERVAL '5 minutes'
 WHERE id IN (SELECT id FROM lead
               WHERE tenant_id = $1 AND state = 'pending' AND next_action_at &lt;= now()
                 AND (owner_agent IS NULL OR lease_expires_at &lt; now())
               ORDER BY score DESC LIMIT $batch FOR UPDATE SKIP LOCKED)
RETURNING *;   -- partial index on state='pending'; the frontier is mostly done rows</div>
</div>

<div class="page">
  <h1>The store</h1>
  <p class="sub">Three shapes, because there are three kinds of truth.</p>
  {store_blocks}

  <h2>Why not one table</h2>
  {split}

  <h2>Vector indexes — PLATFORM-SPEC §6's amendment</h2>
  <div class="code">CREATE VECTOR INDEX artist_chunk_search
  ON artist_chunk (tenant_id, artist_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX artist_fact_search
  ON artist_fact  (tenant_id, artist_id, embedding vector_cosine_ops);</div>
  <p class="note">CockroachDB accelerates a vector-index filter only on prefix columns under
  equality or IN. Artist scoping is always equality and is the dominant filter.
  <span class="mono">platform</span> is deliberately <b>not</b> a prefix column: adding it would
  break every query that does not specify a platform, and post-filtering inside one artist's
  chunks is cheap.</p>
</div>

<div class="page">
  <h1>When the world moves</h1>
  <p class="sub">They had no TikTok. Now they do. Nothing is purged — staleness is a state, and
     it cascades.</p>

  <div class="thesis"><b>A purge destroys exactly the audit trail a label needs when a campaign
  goes sideways.</b> "We believed X on 2026-06-12 because of source S, and that ended on
  2026-08-07" has to stay answerable. So facts carry a status and the change propagates through
  a dependency graph, rather than anything being deleted.</div>

  {svg_cascade()}

  <h2>A fact's status</h2>
  <table><thead><tr><th style="width:14%">Status</th><th>Means</th></tr></thead><tbody>
  {statuses}</tbody></table>
  <p class="note">Two causes of staleness, one handler: the basis changed, or time passed —
  dimension_policy gives each dimension a TTL (BPM unbounded, follower count days, an audience
  hypothesis weeks and <i>should</i> be revised). Precedence: asserted and measured both outrank
  inferred, and an inferred fact may never supersede either — the most it can do is mark one
  stale and ask for a human.</p>

  <h2>The walkthrough</h2>
  <table><thead><tr><th style="width:8%">When</th><th style="width:47%">What happens</th>
  <th>Why it is built that way</th></tr></thead><tbody>
  {rows(TIKTOK_WALK, ['b', '', 'note'])}</tbody></table>

  <h2>Every other kind of change</h2>
  <table><thead><tr><th style="width:22%">Situation</th><th>Handling</th></tr></thead><tbody>
  {rows(CHANGE_CLASSES, ['b', 'note'])}</tbody></table>
</div>

<div class="page">
  <h1>Why it scales</h1>
  <p class="sub">Five axes it grows along, and the three ceilings it actually has.</p>

  <div class="thesis">The scaling argument is <b>not</b> about rows per second. At one label with
  a small roster this database is never stretched, and any claim otherwise would be false. The
  argument is that every axis of growth this business has — more artists, more platforms, more
  throughput, more history, more purposes — is absorbed by <b>data or by another process</b>,
  and none of them by new code in the core.</div>

  <table><thead><tr><th style="width:16%">Axis</th><th style="width:20%">Absorbed by</th>
  <th>How</th></tr></thead><tbody>
  {rows(SCALE_AXES, ['b', 'b note', ''])}</tbody></table>

  <h2>The one that is counter-intuitive</h2>
  <div class="verdict"><b>Most crawlers get more expensive as they accumulate. This one gets
  cheaper per unit of value.</b> <span class="mono">target_hash</span> means a re-crawl skips
  every known target. <span class="mono">content_hash</span> means an unchanged page is never
  re-embedded. <span class="mono">dimension_policy</span> means only volatile facts are ever
  rechecked — BPM is never asked again. And every human verdict makes the scorer more precise, so
  the frontier spends its budget on better candidates than it did last month. More history means
  less work, not more.</div>

  <h2>The ceilings — what actually breaks first</h2>
  <table><thead><tr><th style="width:22%">Ceiling</th><th style="width:10%">Severity</th>
  <th>Reality</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{c}</td><td>{pill(s)}</td><td class='note'>{d}</td></tr>"
           for c, s, d in CEILINGS)}</tbody></table>
  <p class="note">Cost floor: <b>$0.00/month idle.</b> CockroachDB Basic scales to zero and a
  Lambda has no idle floor, so a roster nobody is working costs nothing — measured, not estimated,
  in infra/MEMORY-WORKLOAD.md and platform/README.md.</p>
</div>

<div class="page">
  <h1>What the database is doing</h1>
  <p class="sub">Four jobs in one store, and what each of them buys that a second system would not.</p>

  <table><thead><tr><th style="width:12%">Job</th><th style="width:24%">Here</th>
  <th>What that means</th></tr></thead><tbody>
  {rows(JOBS, ['b', 'b note', ''])}</tbody></table>

  <h2>Provenance of attention — the query an agent with a context-window plan cannot answer</h2>
  <div class="code">WITH RECURSIVE trail AS (
  SELECT id, parent_lead_id, target, reason, 0 AS hop
    FROM lead WHERE id = $1
  UNION ALL
  SELECT l.id, l.parent_lead_id, l.target, l.reason, t.hop + 1
    FROM lead l JOIN trail t ON l.id = t.parent_lead_id)
SELECT hop, target, reason FROM trail ORDER BY hop DESC;</div>
  <p class="note"><span class="mono">fact_basis</span> answers <i>where did this claim come
  from</i>. This answers <i>why were we ever looking there</i> — back to the seed, with the reason
  printed at every hop.</p>

  <h2>What the Forager gives back to the platform spec</h2>
  {gives_back}

  <h2>Where it strains — stated rather than hidden</h2>
  <table><thead><tr><th style="width:30%">Tension</th><th style="width:11%">State</th>
  <th>Response</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{t}</td><td>{pill(s)}</td><td class='note'>{d}</td></tr>"
           for t, s, d in HONEST)}</tbody></table>
  <div class="verdict"><b>The honest verdict on the database is the one PLATFORM-SPEC already
  gave, and it does not change here:</b> at this volume Postgres would serve the workload. What
  CockroachDB buys is one store instead of four, correctness defaults that cannot be forgotten,
  and a changefeed that cannot diverge from the data it reports. That is a real engineering
  reason. It is not a scale reason, and presenting it as one would be the dishonest version of
  this document.</div>
</div>

<div class="page">
  <h1>The submission</h1>
  <p class="sub">What it covers, what it demonstrates, what has actually been checked.</p>

  <h2>Requirement coverage</h2>
  <table><thead><tr><th style="width:20%">Requirement</th><th style="width:36%">Before</th>
  <th>With the Forager</th></tr></thead><tbody>
  {rows(COVERAGE, ['b', 'note', ''])}</tbody></table>

  <h2>Five things a judge can watch happen</h2>
  <table><thead><tr><th style="width:26%">Beat</th><th>What it proves</th></tr></thead><tbody>
  {rows(DEMO, ['b', 'note'])}</tbody></table>

  <h2>Verified against the real cluster, and what is not</h2>
  <table><thead><tr><th style="width:30%">Claim</th><th style="width:11%">Status</th>
  <th>Source or action</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{c}</td><td>{pill(s)}</td><td class='note'>{src}</td></tr>"
           for c, s, src in VERIFIED)}</tbody></table>

  <h2>Risk register</h2>
  <table><thead><tr><th style="width:28%">Risk</th><th style="width:10%">Severity</th>
  <th>Mitigation</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{r}</td><td>{pill(s)}</td><td class='note'>{m}</td></tr>"
           for r, s, m in RISKS)}</tbody></table>
</div>

</body></html>"""


def main() -> None:
    HTML(string=build_html()).write_pdf(OUT)
    print(f"wrote {OUT}")
    print(f"  {sum(len(g[3]) for g in STORE)} tables in {len(STORE)} groups, "
          f"{len(LEAD_KINDS) - 1} lead kinds, {len(SCALE_AXES)} scale axes")
    print(f"  {len(DEMO)} demo beats, "
          f"{sum(1 for v in VERIFIED if v[1] == 'VERIFIED')} verified, "
          f"{sum(1 for v in VERIFIED if v[1] == 'UNVERIFIED')} unverified, "
          f"{len(RISKS)} risks, {len(CEILINGS)} ceilings")


if __name__ == "__main__":
    main()
