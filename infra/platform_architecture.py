#!/usr/bin/env python3
"""Render the platform architecture to PDF, for overview / engineering / audit.

    python3 platform_architecture.py        # -> platform-architecture.pdf

Four pages:

  1. The system     the spine, the substrate, and the fleet, on one picture
  2. The data model the tables, and the four invariants enforced by schema
  3. The loop       the thread state machine, the claim, and who wakes whom
  4. The audit      decision register, verification status, risk register

Generated rather than drawn, for the same reason diagram.py generates its pictures
and workload.py generates its tables: the architecture is described in exactly one
place. Everything the PDF says comes from the data block below. Change the
architecture by changing this file.

Source of record is docs/PLATFORM-SPEC.md; this renders it, and where the two
disagree the spec wins and this file is the bug.

Requires weasyprint (`pip install weasyprint`). No graphviz — the diagrams are
hand-authored SVG so this runs anywhere weasyprint does.
"""

from pathlib import Path

from weasyprint import HTML

OUT = Path(__file__).parent / "platform-architecture.pdf"
SPEC_DATE = "2026-08-06"
DEADLINE = "2026-08-18 17:00 EDT"

# ─────────────────────────────────────────────────────────────── the four jobs

JOBS = [
    ("Memory", "What we know about a track, an artist, a counterparty — and what we have learned",
     "track_character · counterparty_observation · lesson"),
    ("State", "Where every conversation with every counterparty currently stands",
     "campaign · thread · message"),
    ("Coordination", "Which agent owns which work item right now, and for how long",
     "thread.owner_agent · thread.lease_expires_at · outbox"),
    ("Event bus", "Row changes, streamed, as the signal that wakes the next agent",
     "CHANGEFEED FOR thread, outbox, message, counterparty_observation"),
]

# ───────────────────────────────────────────────────────────────── the tables

TABLES = [
    ("Spine", "#0f766e", [
        ("tenant", "id, slug, name", ""),
        ("artist", "id, tenant_id, slug, name, status", "the root — everything compounds here"),
        ("track", "id, tenant_id, artist_id, title, isrc, master_key, status", ""),
    ]),
    ("Derived facts — analysed once", "#7c3aed", [
        ("track_measurement", "bpm, musical_key, drop_ms, hook_start_ms, energy_curve_json, method",
         "MEASURED · one row, permanent"),
        ("track_character", "mood, era, reference_artists[], embedding VECTOR(1024), model_version",
         "INFERRED · versioned via supersedes_id"),
        ("track_rights", "master_owner, publishing_json, splits_json, clearance_state, asserted_by",
         "ASSERTED · a human is accountable"),
        ("artist_audience", "model_version, position_json, confidence, supersedes_id",
         "INFERRED · inherited by tracks"),
    ]),
    ("Counterparty — one shape, five kinds", "#b45309", [
        ("counterparty", "kind, platform, handle, bio, profile_embedding VECTOR(1024)",
         "kind: creator | radio | curator | press | sync"),
        ("counterparty_contact", "channel, address, role, verified", "email | dm | manager_email"),
        ("counterparty_observation",
         "dimension, bucket, share, provenance, source, confidence, error_bar_pp",
         "APPEND ONLY · never overwritten"),
    ]),
    ("Outreach", "#be123c", [
        ("campaign", "artist_id, track_id, channel, goal, state", ""),
        ("thread",
         "campaign_id, counterparty_id, state, next_action_at, owner_agent, lease_expires_at",
         "state machine AND lock, one row"),
        ("message", "direction, channel, subject, body, provider_message_id, idempotency_key",
         "idempotency_key UNIQUE"),
        ("outbox", "kind, payload_json, state, attempts, claimed_by, not_before",
         "the irreversible act, transactionally"),
    ]),
    ("Memory and fleet", "#1d4ed8", [
        ("lesson",
         "scope_kind, scope_id, text, evidence_json, embedding VECTOR(1024), supersedes_id",
         "scope: artist | counterparty_kind | channel | global"),
        ("agent_run", "agent_kind, thread_id, input_json, output_json, model, cost_cents, error",
         "makes the fleet restartable, decisions explainable"),
        ("channel_playbook",
         "channel, state_machine_json, cadence_json, draft_system_prompt, success_metric",
         "a channel is data, not code"),
    ]),
]

INVARIANTS = [
    ("Provenance is structural, not conventional",
     "measured / inferred / asserted get three different tables. Mixing them is a schema "
     "error, not a discipline failure.",
     "track_measurement vs track_character vs track_rights"),
    ("Revisions append; they never overwrite",
     "An estimate can never clobber a measurement. The current value is the head of a chain.",
     "supersedes_id, on every inferred table"),
    ("One open thread per counterparty, across all channels",
     "A creator who is also a curator cannot be worked by two fleets at once. The second "
     "fleet's insert fails and does not need to know the first exists.",
     "CREATE UNIQUE INDEX ... ON thread (tenant_id, counterparty_id) "
     "WHERE state NOT IN ('closed_won','closed_lost','closed_no_reply')"),
    ("A send is recorded in the same transaction that queues it",
     "A crash between 'sent' and 'recorded as sent' would double-email a counterparty — "
     "a relationship you burn exactly once.",
     "message + outbox written in one txn; idempotency_key UNIQUE"),
]

# ───────────────────────────────────────────────────────────────── the fleet

AGENTS = [
    ("Scout", "Find counterparties worth approaching", "track_character, counterparty",
     "counterparty, thread(discovered)"),
    ("Researcher", "Enrich one counterparty", "counterparty", "counterparty_observation"),
    ("Drafter", "Write the outreach", "lesson, track_*, counterparty_*",
     "message(outbound), thread"),
    ("Sender", "Perform the irreversible act", "outbox", "message.sent_at, outbox.state"),
    ("Inbox", "Classify replies, decide next action", "message(inbound)", "thread.state, lesson"),
    ("Negotiator", "Drive rate and terms to agreement", "thread, message, lesson",
     "message(outbound), thread"),
    ("Analyst", "Update the audience model, kill dead hypotheses",
     "counterparty_observation, results", "artist_audience, lesson"),
    ("RemixKit", "Generate the assets a brief needs", "track_*, artist identity",
     "assets, agent_run"),
]

WAKES = [
    ("thread.state -&gt; shortlisted", "Researcher", "enriches the counterparty, writes observations"),
    ("thread.state -&gt; approved", "Drafter", "retrieves lessons, drafts, sets awaiting_human"),
    ("thread.state -&gt; queued", "Drafter", "writes the outbox row — the human has approved"),
    ("outbox insert", "Sender", "sends, records provider_message_id"),
    ("message insert, direction=inbound", "Inbox", "classifies intent, sets the next state"),
    ("counterparty_observation insert", "Analyst", "updates the audience model, may write a lesson"),
]

STATES = ["discovered", "shortlisted", "approved", "drafted", "awaiting_human", "queued",
          "sent", "awaiting_reply", "replied", "negotiating", "agreed", "delivered",
          "verified", "closed"]

# ───────────────────────────────────────────────────────────────── the audit

DECIDED = [
    ("The artist is the spine, not the track", "SCOPE-RESET §1",
     "Counterparty relationships, the audience model and lessons compound on the artist. "
     "A track-rooted schema restarts cold every release."),
    ("A track is analysed once; processes query those facts", "SCOPE-RESET §2",
     "Five outreach kinds share one counterparty shape, so it is one index and one engine, "
     "not six channel tools."),
    ("Processes contribute facts, not only consume them", "SCOPE-RESET §2a",
     "The failure MEMORY-SPEC diagnosed in RemixKit — read-only memory — must not repeat."),
    ("Agents coordinate through the store, not through each other", "PLATFORM-SPEC §0",
     "No orchestrator, no agent-to-agent RPC, no broker."),
    ("CockroachDB, on consolidation and correctness — not scale", "PLATFORM-SPEC §1",
     "At this volume Postgres would serve it. Claiming scale would be the dishonest version."),
    ("Two channels by Aug 18: UGC and radio", "PLATFORM-SPEC §7",
     "One cannot show the substrate is generic; five multiplies integration surface that is "
     "not memory-layer work."),
]

OPEN = [
    ("Counterparty acquisition method", "HIGH — gates the Scout",
     "Pillar 10 §4 is a hard 'no scraper, ever'; §5 finds manual sound-page browsing both "
     "compliant and higher-signal. Human-in-the-loop bulk acceptance is the presumed middle."),
    ("Licence", "MEDIUM — required to submit",
     "Repo has none. Apache-2.0 recommended: patent grant, safer commercial default."),
    ("Repository topology", "LOW — cosmetic until launch",
     "Platform here with RemixKit beneath it, or a new repo with this one vendored."),
    ("Tenancy", "LOW — policy, not schema",
     "tenant_id is on every table regardless. One label, or a product from commit one."),
]

VERIFICATION = [
    ("Vector index available on CockroachDB Basic", "VERIFIED",
     "Vendor pricing page, via infra/MEMORY-WORKLOAD.md (2026-07-26)"),
    ("Basic scales to zero; 50M RU + 10 GiB free per month", "VERIFIED",
     "Vendor pricing page; restated on the Basic cluster planning page (2026-08-06)"),
    ("Changefeeds available on CockroachDB Basic", "VERIFIED",
     "Cockroach's own Basic cluster planning page: 'running a changefeed to an external "
     "sink, also consumes RUs'. Third-party claims that serverless disables changefeeds "
     "date from 2021-22 and are stale. Checked 2026-08-06."),
    ("Vector-index filters accelerate on prefix columns with equality/IN only", "VERIFIED",
     "Vector index docs: 'Index acceleration with filters is only supported if the filters "
     "match prefix columns.' Range comparisons and subqueries fall back to post-filtering. "
     "R1 was amended for this — see reference/COCKROACHDB-AI.md."),
    ("feature.vector_index.enabled settable on a Basic cluster", "UNVERIFIED",
     "GO/NO-GO. Vector indexes require this cluster setting, and cluster settings are "
     "generally restricted on serverless tiers. The entire retrieval design depends on it. "
     "Check first, on day 1, before anything else is built."),
    ("RU cost of a filtered vector scan", "UNVERIFIED",
     "Not published anywhere. Stated as headroom, never as spend. Measure on day 1."),
    ("RU cost of a continuously running changefeed", "UNVERIFIED",
     "Confirmed to consume RUs; the rate is unpublished. Unlike a query this draws "
     "continuously, so it is the likelier of the two to erode the free allowance. "
     "Measure on day 1 alongside the vector scan."),
    ("CockroachDB overage rates past the free tier", "UNVERIFIED",
     "Third-party figures only; both vendor pages defer to a quote. Matters only if free breaks."),
    ("Improvement in outreach cost from accumulated memory", "ASSUMPTION",
     "The one number that would change the conclusion, and the one nothing can produce yet."),
]

RISKS = [
    ("Vector indexes may not be enablable on the Basic tier", "HIGH",
     "Requires SET CLUSTER SETTING feature.vector_index.enabled, and serverless tiers "
     "restrict cluster settings. If it cannot be set, either it is on by default or the "
     "tier is wrong. Day-1 go/no-go, ahead of all other work."),
    ("Filtered ANN degrades to post-filtering on non-prefix predicates", "MEDIUM",
     "Documented, not speculative. Mitigated by denormalising freshness and open-thread "
     "state into counterparty.contact_state, maintained in the same transaction that "
     "opens the thread, so every predicate is equality."),
    ("Twelve days, greenfield, zero CockroachDB code written", "HIGH",
     "Scope expanded to a superset while the clock shrank. Days 5-7 are the core: if "
     "everything after slips, the submission still has its headline criterion."),
    ("Email deliverability and domain warmup", "HIGH",
     "Takes longer than twelve days and scores nothing. The demo sends only to owned and "
     "consented addresses; the outbox proves the mechanism without volume."),
    ("Free-tier RU erosion by the changefeed", "MEDIUM",
     "Continuous draw, unpublished rate. Fallback is polling thread.next_action_at, which "
     "is adequate at this volume and costs the architecture its elegance, not its function."),
    ("Serializable retries under fleet contention", "MEDIUM",
     "Serializable is the reason to choose this database and also a cost: contended "
     "transactions retry and the application must handle it. SKIP LOCKED keeps claim "
     "contention low, but retry loops are not optional."),
    ("Small N on the improvement metric by Aug 18", "MEDIUM",
     "Label the curve with its N. Do not draw a flattering one — the house rule that made "
     "screen_clips.py abstain applies to our own demo."),
    ("Counterparty PII acquires residency obligations", "LOW",
     "A creator index is a database of people. If EU counterparties are indexed at volume, "
     "REGIONAL BY ROW stops being optional. Not a launch blocker."),
]

VERDICT_ROWS = [
    ("Scale", "NEITHER", "Thousands of counterparties, hundreds of sends a day. "
     "Neither engine is stretched. Not a differentiator."),
    ("Vectors in the same transaction", "TIE",
     "pgvector gives this too. It is a reason to avoid Pinecone, not a reason to pick Cockroach."),
    ("Serializable by default", "COCKROACH",
     "Postgres defaults to Read Committed. Two agents writing lessons about one counterparty "
     "silently lose an update. Here the footgun cannot be forgotten — at the cost of retries."),
    ("Changefeeds as a first-class feature", "COCKROACH",
     "Postgres logical decoding plus Debezium is a project. Here it is one DDL statement."),
    ("Scale to zero at $0 idle", "COCKROACH, narrowly",
     "Matches the measured cost model. Neon and Aurora Serverless v2 also scale to zero, "
     "so this is a convenience, not a moat."),
    ("Ecosystem, ORMs, answers to obscure questions", "POSTGRES",
     "PG-wire compatible is not PG-identical. Expect to hit edges."),
]


def svg_system() -> str:
    """Page 1: spine on top, substrate in the middle, fleet below."""
    boxes = []
    for i, (name, *_rest) in enumerate(AGENTS):
        x = 22 + (i % 4) * 172
        y = 400 + (i // 4) * 54
        boxes.append(
            f'<rect x="{x}" y="{y}" width="156" height="40" rx="6" fill="#eef2ff" '
            f'stroke="#1d4ed8"/>'
            f'<text x="{x + 78}" y="{y + 25}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="#1e3a8a">{name}</text>')

    spine = ["tenant", "artist", "track", "derived facts"]
    spine_boxes = []
    for i, name in enumerate(spine):
        x, w = 60 + i * 168, 140
        spine_boxes.append(
            f'<rect x="{x}" y="34" width="{w}" height="38" rx="6" fill="#ccfbf1" '
            f'stroke="#0f766e"/>'
            f'<text x="{x + w / 2}" y="58" text-anchor="middle" font-size="13" '
            f'font-weight="600" fill="#134e4a">{name}</text>')
        if i < len(spine) - 1:
            spine_boxes.append(
                f'<path d="M{x + w} 53 L{x + 162} 53" stroke="#0f766e" stroke-width="2" '
                f'marker-end="url(#arw)"/>')

    jobs = []
    for i, (name, _d, _w) in enumerate(JOBS):
        x = 40 + i * 170
        jobs.append(
            f'<rect x="{x}" y="196" width="150" height="52" rx="6" fill="#ffffff" '
            f'stroke="#0f766e"/>'
            f'<text x="{x + 75}" y="220" text-anchor="middle" font-size="13" '
            f'font-weight="700" fill="#134e4a">{name}</text>'
            f'<text x="{x + 75}" y="238" text-anchor="middle" font-size="10" '
            f'fill="#5b7c78">one transaction</text>')

    return f"""
<svg viewBox="0 0 720 500" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arw" markerWidth="9" markerHeight="9" refX="8" refY="3"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L8,3 z" fill="#0f766e"/></marker>
    <marker id="arwb" markerWidth="9" markerHeight="9" refX="8" refY="3"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L8,3 z" fill="#1d4ed8"/></marker>
  </defs>

  <text x="14" y="20" font-size="11" font-weight="700" fill="#0f766e"
        letter-spacing="1.4">THE SPINE — analysed once</text>
  {''.join(spine_boxes)}

  <rect x="14" y="150" width="692" height="118" rx="10" fill="#f0fdfa" stroke="#0f766e"
        stroke-width="2"/>
  <text x="28" y="176" font-size="11" font-weight="700" fill="#0f766e"
        letter-spacing="1.4">THE SUBSTRATE — CockroachDB, four jobs</text>
  {''.join(jobs)}

  <path d="M363 76 L363 148" stroke="#0f766e" stroke-width="2" marker-end="url(#arw)"/>

  <text x="14" y="300" font-size="11" font-weight="700" fill="#1d4ed8"
        letter-spacing="1.4">THE FLEET — agents do not call each other</text>
  <path d="M250 272 L250 392" stroke="#1d4ed8" stroke-width="2" marker-end="url(#arwb)"
        stroke-dasharray="5 4"/>
  <text x="262" y="326" font-size="11" fill="#1d4ed8">a changefeed wakes an agent</text>
  <path d="M470 392 L470 272" stroke="#1d4ed8" stroke-width="2" marker-end="url(#arwb)"/>
  <text x="262" y="352" font-size="11" fill="#1d4ed8">the agent writes its result back</text>
  {''.join(boxes)}
</svg>"""


def svg_states() -> str:
    """Page 3: the thread state machine."""
    parts = []
    for i, s in enumerate(STATES):
        x = 8 + (i % 5) * 140
        y = 20 + (i // 5) * 62
        fill = "#fee2e2" if s == "closed" else ("#fef3c7" if s == "awaiting_human" else "#eff6ff")
        edge = "#be123c" if s == "closed" else ("#b45309" if s == "awaiting_human" else "#1d4ed8")
        parts.append(
            f'<rect x="{x}" y="{y}" width="126" height="34" rx="17" fill="{fill}" '
            f'stroke="{edge}"/>'
            f'<text x="{x + 63}" y="{y + 22}" text-anchor="middle" font-size="11" '
            f'font-weight="600" fill="#1e293b">{s}</text>')
        if i < len(STATES) - 1:
            if (i % 5) == 4:
                parts.append(
                    f'<path d="M{x + 63} {y + 34} L{x + 63} {y + 48} L71 {y + 48} L71 {y + 62}" '
                    f'fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#arg)"/>')
            else:
                parts.append(
                    f'<path d="M{x + 126} {y + 17} L{x + 137} {y + 17}" stroke="#94a3b8" '
                    f'stroke-width="1.5" marker-end="url(#arg)"/>')
    return f"""
<svg viewBox="0 0 720 205" width="100%" xmlns="http://www.w3.org/2000/svg">
  <defs><marker id="arg" markerWidth="8" markerHeight="8" refX="7" refY="3"
      orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L0,6 L7,3 z" fill="#94a3b8"/></marker></defs>
  {''.join(parts)}
</svg>"""


def rows(items, classes):
    out = []
    for it in items:
        cells = "".join(f"<td class='{c}'>{v}</td>" for c, v in zip(classes, it))
        out.append(f"<tr>{cells}</tr>")
    return "".join(out)


def pill(status):
    key = status.split("—")[0].split(",")[0].strip().split()[0]
    return f"<span class='pill {key}'>{status}</span>"


def build_html() -> str:
    table_blocks = ""
    for group, colour, tbls in TABLES:
        body = "".join(
            f"<tr><td class='mono b'>{n}</td><td class='mono sm'>{c}</td>"
            f"<td class='note'>{note}</td></tr>" for n, c, note in tbls)
        table_blocks += (
            f"<h3 style='color:{colour}'>{group}</h3>"
            f"<table><thead><tr><th style='width:20%'>Table</th>"
            f"<th style='width:48%'>Columns (abridged)</th><th>Note</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")

    inv = "".join(
        f"<div class='inv'><div class='invh'>{i + 1}. {t}</div><div>{d}</div>"
        f"<div class='mono sm cite'>{c}</div></div>"
        for i, (t, d, c) in enumerate(INVARIANTS))

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 12mm 13mm 13mm 13mm;
  @bottom-center {{ content: "Platform Architecture · {SPEC_DATE} · page " counter(page)
    " of " counter(pages); font: 8pt Helvetica; color: #94a3b8; }} }}
body {{ font: 9pt/1.38 Helvetica, Arial, sans-serif; color: #1e293b; }}
h1 {{ font-size: 18pt; margin: 0 0 2pt; letter-spacing: -.4pt; }}
h2 {{ font-size: 11.5pt; margin: 13pt 0 5pt; padding-bottom: 2pt;
      border-bottom: 2px solid #0f766e; color: #0f766e; }}
h3 {{ font-size: 9.5pt; margin: 9pt 0 3pt; }}
.sub {{ color: #64748b; font-size: 9pt; margin: 0 0 2pt; }}
.meta {{ color: #94a3b8; font-size: 7.6pt; margin-bottom: 8pt; }}
.page {{ page-break-after: always; }}
.page:last-child {{ page-break-after: auto; }}
table {{ width: 100%; border-collapse: collapse; margin: 3pt 0 6pt; }}
th {{ text-align: left; font-size: 7.2pt; text-transform: uppercase; letter-spacing: .6pt;
     color: #64748b; border-bottom: 1.5px solid #cbd5e1; padding: 3pt 5pt 2pt; }}
td {{ padding: 3pt 5pt; border-bottom: 1px solid #e2e8f0; vertical-align: top;
      font-size: 8.1pt; }}
.mono {{ font-family: "DejaVu Sans Mono", monospace; }}
.sm {{ font-size: 7pt; }}
.b {{ font-weight: 700; }}
.note {{ color: #64748b; font-size: 7.7pt; }}
.thesis {{ background: #f0fdfa; border-left: 3px solid #0f766e; padding: 6pt 9pt;
           margin: 7pt 0 9pt; font-size: 9.5pt; }}
.inv {{ border: 1px solid #cbd5e1; border-left: 3px solid #0f766e; border-radius: 3px;
        padding: 6pt 10pt; margin: 5pt 0; break-inside: avoid; }}
tr, svg, .code, .verdict, .thesis {{ break-inside: avoid; }}
h2, h3 {{ break-after: avoid; }}
.invh {{ font-weight: 700; margin-bottom: 2pt; }}
.cite {{ color: #0f766e; margin-top: 3pt; }}
.pill {{ font-size: 7.4pt; font-weight: 700; padding: 1.5pt 5pt; border-radius: 8pt; }}
.HIGH, .UNVERIFIED, .POSTGRES {{ background:#fee2e2; color:#991b1b; }}
.MEDIUM, .ASSUMPTION, .TIE, .NEITHER {{ background:#fef3c7; color:#92400e; }}
.LOW, .VERIFIED, .COCKROACH {{ background:#dcfce7; color:#166534; }}
.code {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:3px; padding:7pt 9pt;
         font-family:"DejaVu Sans Mono",monospace; font-size:7.6pt; white-space:pre;
         margin:5pt 0; }}
.verdict {{ background:#fffbeb; border:1px solid #fbbf24; border-radius:4px;
            padding:9pt 12pt; margin:8pt 0; }}
</style></head><body>

<div class="page">
  <h1>Platform Architecture</h1>
  <p class="sub">One artist's catalogue, many outreach channels, one substrate.</p>
  <p class="meta">Generated by infra/platform_architecture.py · {SPEC_DATE} ·
     source of record docs/PLATFORM-SPEC.md · submission deadline {DEADLINE}</p>

  <div class="thesis"><b>Agents do not call each other.</b> They read shared memory, act,
  write the result back, and that write wakes the next agent. There is no orchestrator, no
  agent-to-agent RPC and no message broker. A campaign advances because a row changed.</div>

  {svg_system()}

  <h2>Why one store</h2>
  <table><thead><tr><th style="width:14%">Job</th><th style="width:40%">What it holds</th>
  <th>Where</th></tr></thead><tbody>
  {rows(JOBS, ['b', '', 'mono sm'])}</tbody></table>
  <p class="note">The conventional stack for these four jobs is Postgres + a vector service +
  Redis + a queue: four systems, three seams, and every seam is a place two agents can
  disagree about reality. This is a consolidation and correctness argument — <b>not</b> a
  scale argument. See the verdict on the audit pages.</p>
</div>

<div class="page">
  <h1>The data model</h1>
  <p class="sub">Every fact carries how it was obtained. Processes contribute facts; they do
  not only consume them.</p>
  {table_blocks}
</div>

<div class="page">
  <h1>Invariants and the loop</h1>
  <p class="sub">Four things the schema makes impossible, and the state machine they protect.</p>
  <h2>Four invariants the schema enforces</h2>
  {inv}
  <h2>Thread state machine — channel-agnostic</h2>
  {svg_states()}
  <p class="note">The terminal state collapses three: <span class="mono">closed_won</span>,
  <span class="mono">closed_lost</span>, <span class="mono">closed_no_reply</span>. Only
  these release the counterparty for a future campaign.</p>

  <h2>Claiming work — the one primitive every agent uses</h2>
  <div class="code">UPDATE thread
   SET owner_agent = $agent, lease_expires_at = now() + INTERVAL '5 minutes'
 WHERE id IN (
       SELECT id FROM thread
        WHERE tenant_id = $tenant AND state = $state
          AND next_action_at &lt;= now()
          AND (owner_agent IS NULL OR lease_expires_at &lt; now())
        ORDER BY next_action_at LIMIT $batch
        FOR UPDATE SKIP LOCKED)
RETURNING *;</div>
  <p class="note">A lease, not a lock: an agent that dies mid-task releases its work by
  expiry with no supervisor involved. This is what makes the fleet restartable — and the
  demo's closing beat is killing the fleet mid-campaign and watching every thread resume.</p>
</div>

<div class="page">
  <h1>The fleet</h1>
  <p class="sub">Eight agents. None of them names another.</p>

  <h2>Who wakes whom</h2>
  <table><thead><tr><th style="width:32%">Row change</th><th style="width:14%">Wakes</th>
  <th>Which then</th></tr></thead><tbody>
  {rows(WAKES, ['mono sm', 'b', 'note'])}</tbody></table>

  <h2>What each agent owns</h2>
  <table><thead><tr><th style="width:12%">Agent</th><th style="width:28%">Purpose</th>
  <th style="width:28%">Reads</th><th>Writes</th></tr></thead><tbody>
  {rows(AGENTS, ['b', '', 'mono sm', 'mono sm'])}</tbody></table>
  <p class="note">RemixKit is a tool the Drafter calls, not a peer. The topology above is a
  property of the data, not of any code: no agent imports, addresses or awaits another.</p>
</div>

<div class="page">
  <h1>Audit — the verdict</h1>
  <p class="sub">Whether this is the right database, and what has already been decided.</p>

  <h2>Is CockroachDB the right tool?</h2>
  <div class="verdict"><b>Yes for this system, on a narrow argument — and the narrow
  argument has to be the one presented.</b> At this volume Postgres would serve the
  workload, and any claim otherwise is false. What is bought is one store instead of four,
  and correctness defaults that cannot be forgotten. That is a real engineering reason. It
  is not a scale reason, and presenting it as one would be the dishonest version of this
  document.</div>
  <table><thead><tr><th style="width:25%">Dimension</th><th style="width:17%">Winner</th>
  <th>Why</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{d}</td><td>{pill(w)}</td><td class='note'>{why}</td></tr>"
           for d, w, why in VERDICT_ROWS)}</tbody></table>

  <h2>Decided</h2>
  <table><thead><tr><th style="width:30%">Decision</th><th style="width:14%">Where</th>
  <th>Reasoning</th></tr></thead><tbody>
  {rows(DECIDED, ['b', 'mono sm', 'note'])}</tbody></table>

</div>

<div class="page">
  <h1>Audit — open, verified, at risk</h1>
  <p class="sub">Everything not yet settled, and everything asserted that has not been checked.</p>

  <h2>Open</h2>
  <table><thead><tr><th style="width:23%">Question</th><th style="width:18%">Urgency</th>
  <th>State</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{q}</td><td>{pill(u)}</td><td class='note'>{s}</td></tr>"
           for q, u, s in OPEN)}</tbody></table>

  <h2>Verification status</h2>
  <table><thead><tr><th style="width:27%">Claim</th><th style="width:14%">Status</th>
  <th>Source / action</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{c}</td><td>{pill(s)}</td><td class='note'>{src}</td></tr>"
           for c, s, src in VERIFICATION)}</tbody></table>

</div>

<div class="page">
  <h1>Audit — risk register</h1>
  <p class="sub">Ordered by what would cost the most to discover late.</p>
  <h2>Risks and mitigations</h2>
  <table><thead><tr><th style="width:27%">Risk</th><th style="width:12%">Severity</th>
  <th>Mitigation</th></tr></thead><tbody>
  {''.join(f"<tr><td class='b'>{r}</td><td>{pill(s)}</td><td class='note'>{m}</td></tr>"
           for r, s, m in RISKS)}</tbody></table>
</div>

</body></html>"""


def main() -> None:
    HTML(string=build_html()).write_pdf(OUT)
    print(f"wrote {OUT}")
    print(f"  {len(TABLES)} table groups, {sum(len(t[2]) for t in TABLES)} tables, "
          f"{len(AGENTS)} agents, {len(INVARIANTS)} invariants, {len(STATES)} states")
    print(f"  audit: {len(DECIDED)} decided, {len(OPEN)} open, "
          f"{sum(1 for v in VERIFICATION if v[1].startswith('UNVERIFIED'))} unverified, "
          f"{len(RISKS)} risks")


if __name__ == "__main__":
    main()
