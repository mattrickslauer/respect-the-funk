"""Console views over the real tables.

The promise `demo.py` made was that when a table landed, the fixture would become a
`repo` call and the templates would not change. This is that call. Every function here
returns a `demo.View` — same columns, same chips, same inspector sections — so the
switch is a route edit and nothing below it moves.

Six views are real now: artists, tracks, facts, queue, runs, budgets. Counterparties,
threads, campaigns, approvals and inbox stay on fixtures because their tables do not
exist, and the nav marks which is which. Mixing them is the honest arrangement — it
shows exactly where the substrate stops rather than letting a fixture hide it.

**Empty is a real answer here.** A view with no rows says what would create some,
because an empty table and a broken query look identical otherwise, and the operator
should be able to tell without opening a log.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg

from rtf_platform.demo import Col, Field, Section, View, _bar
from rtf_platform.domain import (
    ARTIST_STATUSES, DEFAULT_TYPE, ArtistType, Platform, ProfileMode, unrecognised,
)

# How many rows a list view pulls. The console is a working surface, not a report: past
# a couple of hundred rows nobody is scanning, they are filtering, and the filter should
# reach the database rather than the browser.
LIMIT = 200


def _rows(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() or {}


def _ago(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strftime("%m-%d %H:%M") if value else "—"


# --------------------------------------------------------------------- facts

def facts(conn: psycopg.Connection, tenant_id: str) -> View:
    rows = _rows(conn, """
        SELECT f.id, f.dimension, f.value_text, f.provenance, f.status, f.confidence,
               f.source, f.written_by, f.observed_at, f.model, f.supersedes_id,
               a.name AS artist_name
          FROM party_fact f
          LEFT JOIN party a ON a.tenant_id = f.tenant_id AND a.id = f.party_id
         WHERE f.tenant_id = %s
         ORDER BY f.observed_at DESC
         LIMIT %s""", (tenant_id, LIMIT))

    counts = _one(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status = 'live')       AS live,
               count(*) FILTER (WHERE status = 'stale')      AS stale,
               count(*) FILTER (WHERE status = 'retracted')  AS retracted
          FROM party_fact WHERE tenant_id = %s""", (tenant_id,))

    glyphs = {"measured": "●", "inferred": "○", "asserted": "◆"}
    out = []
    for r in rows:
        confidence = r["confidence"]
        pct = int(round(float(confidence) * 100)) if confidence is not None else 100
        out.append({
            "id": str(r["id"]),
            "dimension": r["dimension"],
            "value": r["value_text"] or "—",
            "prov": r["provenance"],
            "glyph": glyphs.get(r["provenance"], "·"),
            "conf": f"{float(confidence):.2f}" if confidence is not None else "—",
            "bar": _bar(pct),
            "status": r["status"],
            "agent": r["written_by"] or "—",
            "when": _ago(r, "observed_at"),
            "insp": (
                Section("Claim", "kv", (
                    ("dimension", r["dimension"]),
                    ("value", r["value_text"] or "—"),
                    ("artist", r["artist_name"] or "—"),
                    ("provenance", r["provenance"]),
                    ("status", r["status"]),
                    ("confidence", f"{float(confidence):.2f}" if confidence is not None else "—"),
                    ("source", r["source"] or "—"),
                    ("model", r["model"] or "—"),
                    ("written by", r["written_by"] or "—"),
                    ("observed", _ago(r, "observed_at")),
                )),
                Section("Stands on", "chain", tuple(_basis(conn, "party_fact", r["id"]))),
                Section("Supports", "chain", tuple(_dependents(conn, "party_fact", r["id"]))),
                Section("", "actions", ("Relevant", "Not relevant", "Retract", "Recheck")),
            ),
        })

    return View(
        key="facts", title="Facts",
        blurb="Everything the fleet believes, and what each belief stands on. Live from "
              "party_fact.",
        stats=(("claims", str(counts.get("total", 0)), ""),
               ("live", str(counts.get("live", 0)), ""),
               ("stale", str(counts.get("stale", 0)), ""),
               ("retracted", str(counts.get("retracted", 0)), ""),
               ("shown", str(len(out)), "")),
        cols=(Col("dimension", "Dimension", "b", "22%"), Col("value", "Value", "", "22%"),
              Col("prov", "Prov", "prov", "10%"), Col("conf", "Conf", "num", "6%"),
              Col("bar", "", "bar", "12%"), Col("status", "Status", "chip", "10%"),
              Col("agent", "By", "mono", "9%"), Col("when", "Seen", "mono", "9%")),
        rows=tuple(out),
        empty="No facts yet. Assert one from an artist, or run the forager to measure some.",
    )


def _basis(conn: psycopg.Connection, kind: str, subject_id: Any) -> list[tuple]:
    """The walk up: what this claim stands on. Reads `fact_basis`'s primary key."""
    rows = _rows(conn, """
        SELECT basis_kind, basis_id, weight FROM fact_basis
         WHERE subject_kind = %s AND subject_id = %s
         ORDER BY basis_kind LIMIT 20""", (kind, subject_id))
    if not rows:
        return [(0, "nothing", "no recorded basis", "")]
    return [(0, r["basis_kind"], str(r["basis_id"])[:8] + "…", r["basis_kind"]) for r in rows]


def _dependents(conn: psycopg.Connection, kind: str, basis_id: Any) -> list[tuple]:
    """The walk down: what rests on this. Reads the `fact_basis_down` index — the same
    edges the invalidator follows to mark a cascade stale."""
    rows = _rows(conn, """
        SELECT subject_kind, subject_id FROM fact_basis
         WHERE basis_kind = %s AND basis_id = %s
         ORDER BY subject_kind LIMIT 20""", (kind, basis_id))
    if not rows:
        return [(0, "nothing", "nothing rests on this yet", "")]
    return [(0, r["subject_kind"], str(r["subject_id"])[:8] + "…", r["subject_kind"]) for r in rows]


# --------------------------------------------------------------------- queue

def queue(conn: psycopg.Connection, tenant_id: str) -> View:
    rows = _rows(conn, """
        SELECT l.id, l.kind, l.adapter, l.target, l.depth, l.score, l.state,
               l.owner_agent, l.lease_expires_at, l.next_action_at, l.attempts,
               l.last_error, l.cadence_seconds, l.scope_kind, l.reason,
               l.parent_lead_id, a.name AS artist_name
          FROM lead l
          LEFT JOIN party a ON a.tenant_id = l.tenant_id AND a.id = l.party_id
         WHERE l.tenant_id = %s
         ORDER BY (l.state = 'pending') DESC, l.score DESC, l.next_action_at
         LIMIT %s""", (tenant_id, LIMIT))

    counts = _one(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE state = 'pending') AS pending,
               count(*) FILTER (WHERE state = 'claimed') AS claimed,
               count(*) FILTER (WHERE state = 'failed')  AS failed,
               count(*) FILTER (WHERE state = 'done')    AS done
          FROM lead WHERE tenant_id = %s""", (tenant_id,))

    out = []
    for r in rows:
        score = float(r["score"])
        out.append({
            "id": str(r["id"]),
            "lead": "#" + str(r["id"])[:6],
            "kind": r["kind"],
            "target": r["target"],
            "depth": str(r["depth"]),
            "score": f"{score:.2f}",
            "bar": _bar(int(round(score * 100))),
            "state": r["state"],
            "next": _ago(r, "next_action_at"),
            "insp": (
                Section("Lead", "kv", (
                    ("id", str(r["id"])[:8] + "…"), ("kind", r["kind"]),
                    ("adapter", r["adapter"]), ("scope", r["scope_kind"]),
                    ("artist", r["artist_name"] or "—"),
                    ("state", r["state"]), ("owner", r["owner_agent"] or "—"),
                    ("lease expires", _ago(r, "lease_expires_at")),
                    ("attempts", str(r["attempts"])),
                    ("score", f"{score:.2f}"), ("depth", str(r["depth"])),
                    ("cadence", f"{r['cadence_seconds']}s" if r["cadence_seconds"] else "one-shot"),
                )),
                Section("Target", "quote", (r["target"],)),
                Section("Why we are looking here", "chain",
                        tuple(_trail(conn, tenant_id, r["id"]))),
                *((Section("Last error", "quote", (r["last_error"],)),)
                  if r["last_error"] else ()),
                Section("", "actions", ("Run now", "Release lease", "Reject", "Boost")),
            ),
        })

    return View(
        key="queue", title="Queue",
        blurb="The frontier. Every lead scored, leased, deduped, and remembering the "
              "lead that produced it. Live from lead.",
        stats=(("total", str(counts.get("total", 0)), ""),
               ("pending", str(counts.get("pending", 0)), ""),
               ("claimed", str(counts.get("claimed", 0)), ""),
               ("failed", str(counts.get("failed", 0)), ""),
               ("done", str(counts.get("done", 0)), "")),
        cols=(Col("lead", "Lead", "mono b", "9%"), Col("kind", "Kind", "chip", "13%"),
              Col("target", "Target", "", "34%"), Col("depth", "D", "num", "4%"),
              Col("score", "Score", "num", "7%"), Col("bar", "", "bar", "11%"),
              Col("state", "State", "chip", "11%"), Col("next", "Next", "mono", "11%")),
        rows=tuple(out),
        empty="The frontier is empty. Seed it from an artist's profiles.",
    )


def _trail(conn: psycopg.Connection, tenant_id: str, lead_id: Any) -> list[tuple]:
    """The attention trail — a recursive walk up `parent_lead_id`.

    This is the query that answers *why were we even looking here?* for any document,
    fact or contact the system holds. It is the provenance record and, for a
    counterparty, the compliance artifact at the same time.

    `tenant_id` is carried into the anchor *and* the recursive leg — not just the
    anchor — because `parent_lead_id` is a bare `UUID` with no tenant predicate of its
    own, so an unscoped recursive leg would happily walk into another tenant's `lead`
    row if two tenants' parent chains ever collided on an id (they cannot today, `lead`
    is keyed by a random UUID, but the walk should not depend on that for its scoping).
    """
    rows = _rows(conn, """
        WITH RECURSIVE up(id, parent_lead_id, kind, target, depth, hop) AS (
            SELECT id, parent_lead_id, kind, target, depth, 0
              FROM lead WHERE tenant_id = %s AND id = %s
            UNION ALL
            SELECT l.id, l.parent_lead_id, l.kind, l.target, l.depth, up.hop + 1
              FROM lead l JOIN up ON l.id = up.parent_lead_id
             WHERE l.tenant_id = %s AND up.hop < 8
        )
        SELECT kind, target, depth, hop FROM up ORDER BY hop""",
        (tenant_id, lead_id, tenant_id))
    return [(r["hop"], r["kind"], f"{r['target'][:70]} · depth {r['depth']}", "lead")
            for r in rows] or [(0, "seed", "no parent — this is a seed", "")]


# ---------------------------------------------------------------------- runs

def runs(conn: psycopg.Connection, tenant_id: str) -> View:
    rows = _rows(conn, """
        SELECT r.id, r.agent_kind, r.state, r.summary, r.error, r.documents, r.facts,
               r.metrics, r.leads, r.dropped, r.tokens_in, r.tokens_out,
               r.cost_micro_usd, r.refused_json, r.duration_ms, r.started_at,
               a.name AS artist_name
          FROM agent_run r
          LEFT JOIN party a ON a.tenant_id = r.tenant_id AND a.id = r.party_id
         WHERE r.tenant_id = %s
         ORDER BY r.started_at DESC LIMIT %s""", (tenant_id, LIMIT))

    counts = _one(conn, """
        SELECT count(*) AS total,
               -- `failed` is an agent raising `LeadFailed`; `error` is one raising
               -- anything else. Both are the work not getting done, and counting only
               -- the second made a frontier full of 503s look clean.
               count(*) FILTER (WHERE state IN ('error', 'failed')) AS errors,
               count(*) FILTER (WHERE state = 'refused')            AS refused,
               -- A run whose claim stopped being current: money spent on a fetch whose
               -- writes were thrown away. Invisible until it had its own stat, which is
               -- how a livelock burning the ceiling looked like a busy fleet.
               count(*) FILTER (WHERE state = 'lease_lost')         AS lease_lost,
               coalesce(sum(cost_micro_usd), 0)                     AS micro
          FROM agent_run
         WHERE tenant_id = %s AND started_at > now() - INTERVAL '24 hours'""",
        (tenant_id,))

    out = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "at": _ago(r, "started_at"),
            "agent": r["agent_kind"],
            "what": r["summary"] or r["error"] or "—",
            "dur": f"{r['duration_ms']}ms",
            "tok": str(r["tokens_in"] + r["tokens_out"]),
            "result": r["state"],
            "insp": (
                Section("Run", "kv", (
                    ("id", str(r["id"])[:8] + "…"), ("agent", r["agent_kind"]),
                    ("artist", r["artist_name"] or "—"), ("state", r["state"]),
                    ("duration", f"{r['duration_ms']}ms"),
                    ("tokens in / out", f"{r['tokens_in']} / {r['tokens_out']}"),
                    # Micro-dollars stored as an integer so summing money never drifts.
                    ("cost", f"${r['cost_micro_usd'] / 1_000_000:.6f}"),
                    ("started", _ago(r, "started_at")),
                )),
                Section("Produced", "kv", (
                    ("documents", str(r["documents"])), ("facts", str(r["facts"])),
                    ("metrics", str(r["metrics"])), ("new leads", str(r["leads"])),
                    ("dropped", str(r["dropped"])),
                )),
                *((Section("Error", "quote", (r["error"],)),) if r["error"] else ()),
                *((Section("Refused by the spend gate", "quote",
                           (str(r["refused_json"]),)),) if r["refused_json"] else ()),
                Section("", "actions", ("Replay", "Open lead")),
            ),
        })

    micro = int(counts.get("micro", 0) or 0)
    return View(
        key="runs", title="Runs & errors",
        blurb="Every action every agent took, what it cost, and what broke. "
              "Restartability and explainability come from the same rows.",
        stats=(("runs / 24h", str(counts.get("total", 0)), ""),
               ("errors", str(counts.get("errors", 0)), ""),
               ("refused", str(counts.get("refused", 0)), ""),
               ("lease lost", str(counts.get("lease_lost", 0)), ""),
               ("spend / 24h", f"${micro / 1_000_000:.4f}", ""),
               ("shown", str(len(out)), "")),
        cols=(Col("at", "Time", "mono", "11%"), Col("agent", "Agent", "b", "12%"),
              Col("what", "What", "", "42%"), Col("dur", "Dur", "num", "8%"),
              Col("tok", "Tokens", "num", "9%"), Col("result", "Result", "chip", "10%")),
        rows=tuple(out),
        empty="No runs yet. Nothing has claimed a lead.",
    )


# ------------------------------------------------------------------- budgets

def budgets(conn: psycopg.Connection, tenant_id: str) -> View:
    rows = _rows(conn, """
        SELECT a.id, a.name, a.slug,
               coalesce(b.max_tokens_per_hour, 20000) AS cap,
               coalesce(b.paused, false)              AS paused,
               coalesce(b.max_depth, 3)               AS max_depth,
               coalesce(b.max_leads_per_run, 25)      AS max_leads,
               (SELECT coalesce(sum(tokens_in + tokens_out), 0) FROM agent_run r
                 WHERE r.tenant_id = a.tenant_id AND r.party_id = a.id
                   AND r.started_at > now() - INTERVAL '1 hour')
                 AS spent,
               (SELECT coalesce(sum(cost_micro_usd), 0) FROM agent_run r
                 WHERE r.tenant_id = a.tenant_id AND r.party_id = a.id
                   AND r.started_at > now() - INTERVAL '24 hours')
                 AS micro,
               (SELECT count(*) FROM lead l
                 WHERE l.tenant_id = a.tenant_id AND l.party_id = a.id
                   AND l.state = 'pending') AS pending
          FROM party a
          JOIN party_role pr ON pr.tenant_id = a.tenant_id AND pr.party_id = a.id
                             AND pr.role = 'roster_artist'
          LEFT JOIN party_budget b ON b.tenant_id = a.tenant_id AND b.party_id = a.id
         WHERE a.tenant_id = %s ORDER BY a.name""", (tenant_id,))

    out = []
    for r in rows:
        cap = int(r["cap"]) or 1
        spent = int(r["spent"])
        pct = min(100, int(round(spent / cap * 100)))
        state = "paused" if r["paused"] else ("near cap" if pct >= 90 else "ok")
        out.append({
            "id": str(r["id"]), "artist": r["name"], "scope": "artist",
            "spent": f"{spent:,}", "cap": f"{cap:,}", "pct": f"{pct}%",
            "bar": _bar(pct), "state": state,
            "leads": str(r["pending"]),
            "cost": f"${int(r['micro']) / 1_000_000:.4f}",
            "insp": (
                Section("Budget", "kv", (
                    ("scope", f"artist · {r['name']}"), ("window", "rolling 1h"),
                    ("tokens", f"{spent:,} of {cap:,}"),
                    ("max depth", str(r["max_depth"])),
                    ("max leads per run", str(r["max_leads"])),
                    ("pending leads", str(r["pending"])),
                    ("spend / 24h", f"${int(r['micro']) / 1_000_000:.6f}"),
                    ("paused", "yes" if r["paused"] else "no"),
                )),
                Section("How this is measured", "note", (
                    "Summed from agent_run rather than decremented from a counter. A "
                    "counter row is a serialization point under SERIALIZABLE, and ten "
                    "workers on one launching artist would retry against each other on "
                    "exactly the row meant to protect them.",
                )),
                Section("", "actions", ("Raise cap", "Pause artist", "View runs")),
            ),
        })

    return View(
        key="budgets", title="Budgets",
        blurb="What each scope may spend, what it spent, and what it dropped when it "
              "ran out. Spend is summed from agent_run.",
        stats=(("artists", str(len(out)), ""),
               ("paid calls", "disabled", ""),
               ("ceiling", "$0.00", ""),
               ("spend / 24h", "$0.0000", ""),
               ("idle db cost", "$0.00", "")),
        cols=(Col("artist", "Scope", "b", "22%"), Col("scope", "Tier", "chip", "9%"),
              Col("spent", "Spent", "num", "10%"), Col("cap", "Cap", "num", "9%"),
              Col("bar", "", "bar", "14%"), Col("pct", "%", "num", "6%"),
              Col("leads", "Leads", "num", "8%"), Col("cost", "Cost", "num", "10%"),
              Col("state", "State", "chip", "12%")),
        rows=tuple(out),
        empty="No artists on the roster yet.",
    )


# -------------------------------------------------------------------- tracks

def tracks(conn: psycopg.Connection, tenant_id: str) -> View:
    # A recording is not owned by an artist — credits are, which is what lets one
    # recording carry two main artists instead of being stored twice. So the
    # performer comes from `party_credit`, and `string_agg` because there can be
    # more than one and picking the first would quietly hide the collaborator.
    rows = _rows(conn, """
        SELECT t.id, t.title, t.slug, t.isrc, t.released_on, t.status,
               coalesce(string_agg(a.name, ', ' ORDER BY a.name), '—') AS artist_name,
               (SELECT count(*) FROM party_fact f
                 WHERE f.tenant_id = t.tenant_id AND f.recording_id = t.id)     AS facts,
               (SELECT count(*) FROM lead l
                 WHERE l.tenant_id = t.tenant_id AND l.recording_id = t.id)     AS leads,
               (SELECT count(*) FROM presence pr
                 WHERE pr.tenant_id = t.tenant_id AND pr.subject_kind = 'recording'
                   AND pr.subject_id = t.id AND pr.state = 'present')           AS places
          FROM recording t
          LEFT JOIN party_credit c ON c.tenant_id = t.tenant_id
                                  AND c.subject_kind = 'recording'
                                  AND c.subject_id = t.id
                                  AND c.role IN ('main_artist', 'featured')
          LEFT JOIN party a ON a.tenant_id = t.tenant_id AND a.id = c.party_id
         WHERE t.tenant_id = %s
         GROUP BY t.id, t.title, t.slug, t.isrc, t.released_on, t.status
         ORDER BY t.title""", (tenant_id,))

    out = [{
        "id": str(r["id"]), "title": r["title"], "artist": r["artist_name"],
        "state": r["status"], "bpm": "—", "key": "—",
        "campaigns": str(r["leads"]), "streams": str(r["places"]),
        "spark": "▁▁▁▁▁▁▁",
        "insp": (
            Section("Recording", "kv", (
                ("title", r["title"]), ("credited", r["artist_name"]),
                ("isrc", r["isrc"] or "—"),
                ("released", str(r["released_on"]) if r["released_on"] else "—"),
                ("status", r["status"]), ("facts", str(r["facts"])),
                ("leads", str(r["leads"])), ("platforms", str(r["places"])),
            )),
            Section("Not analysed yet", "note", (
                "Measured facts — bpm, key, hook window — come from analysing the master "
                "once. Nothing downstream can query them until that runs.",
            )),
            Section("", "actions", ("Analyse", "Seed frontier", "Edit")),
        ),
    } for r in rows]

    return View(
        key="tracks", title="Tracks",
        blurb="Recordings — the masters, identified by ISRC. Credits decide who they "
              "belong to, so a collaboration is one row. Live from recording.",
        stats=(("tracks", str(len(out)), ""), ("analysed", "0", ""),
               ("with leads", str(sum(1 for r in out if r["campaigns"] != "0")), ""),
               ("facts", str(sum(int(r["insp"][0].items[5][1]) for r in out)), ""),
               ("shown", str(len(out)), "")),
        cols=(Col("title", "Track", "b", "24%"), Col("artist", "Artist", "", "20%"),
              Col("state", "Status", "chip", "12%"), Col("bpm", "BPM", "num", "8%"),
              Col("key", "Key", "mono", "10%"), Col("campaigns", "Leads", "num", "8%"),
              Col("streams", "On", "num", "9%"), Col("spark", "", "spark", "9%")),
        rows=tuple(out),
        empty="No recordings yet. One arrives with an ISRC, and the ISRC is what "
              "places it on every platform.",
    )


# ----------------------------------------------------------------- statements

def _upload_sections(*, error: str = "", note: str = "",
                     pending_token: str = "") -> tuple[Section, ...]:
    """The import form, and whatever the last attempt had to say about itself.

    `pending_token` is set once a file has been previewed and refused only because
    its reader is unverified. Re-submitting with the box ticked is the second,
    deliberate act — the first upload cannot both preview and write.
    """
    from rtf_platform import distributors

    readable = ", ".join(sorted({f.distributor for f in distributors.FORMATS}))
    return (
        Section(
            "Import a statement", "form", (
                Field("file", "Statement file", "file", required=True,
                      placeholder=".tsv,.csv,.txt",
                      hint="The export from your distributor. Nothing is fetched — "
                           "DistroKid has no API, so the file is the interface."),
                Field("distributor", "Distributor", "select", "distrokid",
                      (("", tuple(distributors.KNOWN_DISTRIBUTORS)),),
                      hint=f"Readable today: {readable}. The rest need one real "
                           "export each before a column map can be written."),
                Field("confirm_unverified", "I accept an unchecked column map",
                      "check", pending_token,
                      hint="No reader has been run against a real export yet, so a "
                           "column could be mapped wrongly and still look right."),
            ),
            action="/imports", submit="Import", multipart=True,
            error=error, note=note,
        ),
        Section("Why a file", "note", (
            "Stream counts do not come from a streaming API. They come back down the "
            "supply chain as DSR — monthly, per ISRC, per territory — and a label "
            "holds whatever its distributor passes on. Importing one is also what "
            "seeds the catalogue: every ISRC in the file becomes a recording, and "
            "that is what the platform probe fans out over.",
        )),
    )


def imports(conn: psycopg.Connection, tenant_id: str) -> View:
    from rtf_platform import statements

    rows = statements.recent(conn, tenant_id)
    out = []
    for r in rows:
        period = "—"
        if r["period_start"]:
            period = str(r["period_start"])[:7]
            if r["period_end"] and str(r["period_end"])[:7] != period:
                period += f" → {str(r['period_end'])[:7]}"
        out.append({
            "id": str(r["id"]),
            "file": r["filename"] or "(unnamed)",
            "distributor": r["distributor"],
            "period": period,
            "rows": str(r["rows_loaded"]),
            "plays": f"{r['total_quantity']:,}",
            "money": f"{r['total_earnings']:.2f}",
            "state": "unchecked" if not r["format_verified"] else r["state"],
            "when": r["created_at"].strftime("%m-%d %H:%M"),
            "insp": (
                Section("Import", "kv", (
                    ("file", r["filename"] or "—"),
                    ("distributor", r["distributor"]),
                    ("read as", r["format_key"]),
                    ("column map", "confirmed against a real export"
                     if r["format_verified"] else "never checked against a real export"),
                    ("period", period),
                    ("rows read", str(r["rows_read"])),
                    ("rows loaded", str(r["rows_loaded"])),
                    ("no usable ISRC", str(r["rows_no_isrc"])),
                    ("recordings created", str(r["recordings_created"])),
                    ("plays", f"{r['total_quantity']:,}"),
                    ("earnings", f"{r['total_earnings']:.2f} {r['currency']}"),
                    ("imported by", r["imported_by"] or "—"),
                )),
                Section("Names nobody claimed", "editlist",
                        tuple((name, "no roster party with this exact name", None)
                              for name in (r["unmatched_artists"] or []))
                        or ((None, "Every artist in the file matched the roster.",
                             None),)),
                Section("Note", "note", (
                    "A name is not an identity, so an unmatched one is reported "
                    "rather than turned into a party. Creating one from a string is "
                    "how a roster quietly acquires three spellings of one act.",
                )),
            ),
        })

    return View(
        key="imports", title="Statements",
        blurb="What the distributor actually paid, per recording per territory per "
              "month. The only source of real stream counts. Live from "
              "statement_import.",
        stats=(("imports", str(len(out)), ""),
               ("plays", f"{sum(r['total_quantity'] for r in rows):,}", ""),
               ("earnings", f"{sum((r['total_earnings'] for r in rows), Decimal(0)):.2f}", ""),
               ("recordings made", str(sum(r["recordings_created"] for r in rows)), ""),
               ("unclaimed names",
                str(len({n for r in rows for n in (r["unmatched_artists"] or [])})), "")),
        cols=(Col("file", "File", "b", "24%"), Col("distributor", "Distributor", "chip", "13%"),
              Col("period", "Period", "mono", "13%"), Col("rows", "Rows", "num", "8%"),
              Col("plays", "Plays", "num", "11%"), Col("money", "Earnings", "num", "11%"),
              Col("state", "Map", "chip", "11%"), Col("when", "When", "mono", "9%")),
        rows=tuple(out),
        empty="No statements imported. Export one from your distributor and drop it "
              "in — it is the only place real stream counts come from.",
    )


# ------------------------------------------------------------- artist editing

def _flat(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """A select with no <optgroup>, in the grouped shape the renderer expects."""
    return (("", pairs),)


def _type_options(current: str) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    """The supported types, plus the artist's own if this build has retired it.

    Without that last group, opening the editor to fix a typo in the name would
    silently reclassify the act to whatever the select happened to land on. The
    value survives an unrelated edit because it is offered back, already selected.
    """
    groups = [(group, tuple((t.value, t.label) for t in members))
              for group, members in ArtistType.grouped()]
    legacy = unrecognised(current)
    if legacy:
        groups.append(("No longer offered", ((legacy, legacy),)))
    return tuple(groups)


_MODE_OPTIONS = _flat(tuple((m.value, f"{m.label} — {m.hint}") for m in ProfileMode))
_PLATFORM_OPTIONS = _flat(tuple((p.value, p.label) for p in Platform))


def _identity_fields(name: str, type_: str) -> tuple[Field, ...]:
    return (
        Field("name", "Name", "text", name, required=True,
              hint="The URL key is derived from this; you do not set it."),
        Field("type", "Type", "select", type_, _type_options(type_),
              hint="A band, a DJ and an orchestra are all artists — this is which kind."),
    )


def new_artist_sections(*, form: dict[str, str] | None = None,
                        error: str = "") -> tuple[Section, ...]:
    """The inspector when `?sel=new`. Same pane, same fields, one fewer of them —
    status is not offered because an artist nobody has saved cannot be paused."""
    form = form or {}
    return (
        Section("New artist", "form",
                _identity_fields(form.get("name", ""),
                                 form.get("type", DEFAULT_TYPE.value)),
                action="/artists", submit="Add artist", error=error),
        Section("", "actions", (("Cancel", "/artists", ""),)),
        Section("Note", "note", (
            "Saving here creates the label row too, on the first artist. There is no "
            "seed file to run and no fixture to clear out.",
        )),
    )


def artist_editor_sections(
    artist: dict[str, Any],
    profiles: list[dict[str, Any]],
    counts: dict[str, Any],
    *,
    error: str = "",
    profile_error: str = "",
    confirm_delete: bool = False,
    suggestions: list[dict[str, Any]] | None = None,
) -> tuple[Section, ...]:
    """Everything the inspector shows for one artist: read it, edit it, delete it.

    This used to be four read-only blocks and three buttons that did nothing, with the
    real editing a page away at `/roster`. Navigating away to change a name costs the
    selection, the scroll position and the comparison you were in the middle of — so
    the write lives where the record is already open.
    """
    aid = str(artist["id"])

    edit = Section(
        "Artist", "form",
        _identity_fields(artist["name"], artist["type"]) + (
            Field("status", "Status", "select", artist["status"],
                  _flat(tuple((s, s) for s in ARTIST_STATUSES)),
                  hint="Paused keeps the history and stops the agents."),
        ),
        action=f"/artists/{aid}", submit="Save", error=error,
    )

    record = Section("Record", "kv", (
        ("slug", artist["slug"]),
        ("added", artist["created_at"].strftime("%Y-%m-%d")),
        ("tracks", str(counts["tracks"])),
        ("live facts", str(counts["facts"])),
        ("documents", str(counts["docs"])),
        ("pending leads", str(counts["pending"])),
    ))

    #: Each profile is its own delete target. Rendered as a list with a control per row
    #: rather than a multi-select, because removing the wrong surface is silent — the
    #: artist simply stops being researched there and nothing announces it.
    where = Section(
        "Where we look", "editlist",
        tuple(
            (p["platform"],
             f"{p['mode']} · {p['handle'] or p['profile_url'] or 'no handle'}",
             f"/artists/{aid}/profiles/{p['id']}/delete")
            for p in profiles
        ) or ((None, "Nothing configured — the forager has nowhere to start.", None),),
    )

    #: Directly beneath the surfaces they are candidates for, so accepting one reads as
    #: promoting a row in a list the operator is already looking at — not as a separate
    #: workflow they have to remember exists.
    suggested = suggestions_section(suggestions or [], back=f"/artists?sel={aid}")

    add = Section(
        "Add a surface", "form", (
            Field("platform", "Platform", "select", "", _PLATFORM_OPTIONS),
            Field("mode", "Mode", "select", ProfileMode.OWNED.value, _MODE_OPTIONS),
            Field("handle", "Handle", "text", "", placeholder="@example"),
            Field("profile_url", "URL", "url", "", placeholder="https://…"),
        ),
        action=f"/artists/{aid}/profiles", submit="Add surface", error=profile_error,
    )

    note = Section("Note", "note", (
        "An artist with no account on a platform is still worth searching there — fan "
        "activity is content about them whether they take part or not. That is why "
        "Absent is a mode rather than a missing row.",
    ))

    if confirm_delete:
        danger = Section(
            "Delete", "form", (
                Field("", "", "static",
                      f"Deleting {artist['name']} also deletes "
                      f"{counts['tracks']} tracks, {counts['facts']} live facts, "
                      f"{counts['docs']} documents, {len(profiles)} surfaces and every "
                      "lead beneath them. This cannot be undone."),
            ),
            action=f"/artists/{aid}/delete", submit="Yes, delete permanently",
            tone="danger",
        )
        cancel = Section("", "actions", (("Cancel", f"/artists?sel={aid}", ""),))
        # First, not last. The control that asks for the confirmation sits at the
        # bottom of a scrolling pane, so a confirmation rendered in place would appear
        # below the fold and read as a click that did nothing.
        return (danger, cancel, edit, record, where, suggested, add, note)

    #: A link, not a submit. Deleting an artist cascades, so it takes two deliberate
    #: acts and the second one names what goes with it.
    danger = Section("", "actions", (
        ("Delete artist", f"/artists?sel={aid}&confirm=delete", "d"),
    ))
    return (edit, record, where, suggested, add, note, danger)


# --------------------------------------------------------------- suggestions

#: A suggestion is what an agent produces when it matched by inference rather than
#: measurement — a name search that found a plausible artist, not a page an operator
#: asserted. Nothing promotes one automatically, by design: `SCOPE-RESET` open decision 4
#: settled on a human-in-the-loop scout that surfaces candidates for bulk acceptance,
#: because Pillar 10 §4's verdict on scraping was "no scraper, ever" and §5 found that a
#: person confirming a match is both compliant and the higher-signal path.
#:
#: These render into two surfaces that already exist — the needs-you queue and the artist
#: inspector — rather than a page of their own. A decision queue nobody passes on their
#: way to something else is a decision queue nobody empties.

def pending_suggestions(conn: psycopg.Connection, tenant_id: str,
                        party_id: str | None = None) -> list[dict[str, Any]]:
    where = "s.tenant_id = %s AND s.state = 'pending'"
    params: tuple[Any, ...] = (tenant_id,)
    if party_id:
        where += " AND s.party_id = %s"
        params += (party_id,)
    return _rows(conn, f"""
        SELECT s.id, s.party_id, s.kind, s.payload, s.confidence, s.rationale,
               p.name AS party_name, p.slug AS party_slug
          FROM suggestion s
          JOIN party p ON p.tenant_id = s.tenant_id AND p.id = s.party_id
         WHERE {where}
         ORDER BY p.name, s.confidence DESC, s.created_at
         LIMIT {LIMIT}""", params)


def _suggestion_row(row: dict[str, Any], back: str) -> tuple[Any, Any, Any]:
    """One `editlist` entry: what was found, how sure, and the two ways to answer.

    The confidence is rendered next to the label rather than hidden in the inspector,
    because 0.30 and 0.70 are the difference between "probably them" and "an artist who
    happens to share three letters", and an operator clicking Accept is entitled to see
    which one they are looking at without a second click.
    """
    payload = row["payload"] or {}
    evidence = payload.get("evidence") or {}
    detail = " · ".join(filter(None, (
        payload.get("platform", ""),
        f"{row['confidence']:.2f} confidence",
        f"{evidence.get('fans')} fans" if evidence.get("fans") is not None else "",
        f"{evidence.get('albums')} albums" if evidence.get("albums") is not None else "",
    )))
    sid = str(row["id"])
    return (
        payload.get("label") or payload.get("value") or "candidate",
        detail,
        (("Accept", f"/suggestions/{sid}/accept?back={back}", "p"),
         ("Reject", f"/suggestions/{sid}/reject?back={back}", "d")),
    )


def suggestions_section(rows: list[dict[str, Any]], *, back: str) -> Section:
    """The candidates block, shaped like the surfaces block it sits next to.

    Same `editlist` renderer as "Where we look", deliberately: a suggested surface and a
    configured one are the same kind of thing at different stages of certainty, and
    showing them in two different shapes would imply a distinction that is not there.
    """
    return Section(
        "Suggested surfaces", "editlist",
        tuple(_suggestion_row(r, back) for r in rows) or
        ((None, "Nothing pending — every candidate has been answered.", None),),
    )


# --------------------------------------------------------------- counterparties

def counterparties(conn: psycopg.Connection, tenant_id: str) -> View:
    """Everyone we could take a record to, and what we actually know about each.

    Live from `party` where `party_class = 'counterparty'` — the same table the roster
    lives in, which is the whole argument of migration 009. The columns that differ are
    the ones the shortlist needs: whether they are contactable, and whether they have an
    embedding, because a counterparty without one is invisible to R1 no matter how good a
    match they would be.

    `searchable` is shown as its own column rather than folded into a status, because
    "we know about them but cannot find them" is a specific, fixable state and a reader
    should be able to count them at a glance.
    """
    rows = _rows(conn, """
        SELECT p.id, p.name, p.contact_state, p.embedding_model,
               (p.profile_embedding IS NOT NULL) AS searchable,
               pr.platform, pr.url,
               (SELECT count(*) FROM party_role r
                 WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id) AS roles,
               (SELECT string_agg(r.role, ', ') FROM party_role r
                 WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id) AS role_list,
               (SELECT d.body FROM party_document d
                 WHERE d.tenant_id = p.tenant_id AND d.party_id = p.id
                 ORDER BY d.fetched_at DESC LIMIT 1) AS profile
          FROM party p
          LEFT JOIN presence pr ON pr.tenant_id = p.tenant_id
                                AND pr.subject_kind = 'party' AND pr.subject_id = p.id
         WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
         ORDER BY p.name
         LIMIT %s""", (tenant_id, LIMIT))

    out = []
    for r in rows:
        profile = (r["profile"] or "").strip()
        out.append({
            "id": str(r["id"]), "who": r["name"],
            "kind": r["role_list"] or "—",
            "platform": r["platform"] or "—",
            "state": r["contact_state"],
            "searchable": "yes" if r["searchable"] else "no",
            "spark": "▁▁▁▁▁▁▁",
            "insp": (
                Section("Counterparty", "kv", (
                    ("name", r["name"]),
                    ("roles", r["role_list"] or "none recorded"),
                    ("platform", r["platform"] or "—"),
                    ("contact state", r["contact_state"]),
                    ("searchable", "yes" if r["searchable"]
                     else "no — not embedded, so R1 cannot see them"),
                    ("embedding model", r["embedding_model"] or "—"),
                )),
                Section("What we read", "quote", (profile[:600] or
                        "Nothing recorded. Without a profile there is nothing to embed.",)),
                Section("How they were found", "note", (
                    "Discovered by searching a public playlist index for this artist's "
                    "genre, then aggregated per curator. Nothing here was scraped and "
                    "nothing here is a contact — a name and a public profile is not "
                    "permission to email somebody.",
                )),
                Section("", "actions", (
                    (("Open profile", r["url"], "") if r["url"] else ("No URL", "#", "")),
                )),
            ),
        })

    embedded = sum(1 for r in out if r["searchable"] == "yes")
    return View(
        key="counterparties", title="Counterparties",
        blurb="Curators, programmers and writers we could take a record to. Live from "
              "party where the class is counterparty — the same table the roster is in.",
        stats=(("known", str(len(out)), ""),
               ("searchable", str(embedded), ""),
               ("contactable", str(sum(1 for r in out if r["state"] == "contactable")), ""),
               ("shown", str(len(out)), "")),
        cols=(Col("who", "Who", "b", "30%"), Col("kind", "Role", "chip", "14%"),
              Col("platform", "Platform", "", "12%"),
              Col("state", "Contact", "chip", "14%"),
              Col("searchable", "Indexed", "chip", "10%"),
              Col("spark", "", "spark", "10%")),
        rows=tuple(out),
        empty="Nobody discovered yet. Map a source for an artist, then run prospecting — "
              "curators come from the playlists that already carry their genre.",
    )


# ----------------------------------------------------------------------- today

def today(conn: psycopg.Connection, tenant_id: str) -> tuple[list[dict[str, Any]],
                                                             tuple[tuple[str, str], ...]]:
    """The needs-you queue, from rows rather than fixtures.

    Two things land here today, and both are things the fleet genuinely will not decide:
    an inferred match that needs confirming, and a lead that has been parked because it
    failed for a reason a human has to remove. Everything else the fleet already did, and
    an empty queue is the correct and common state — the template says so and it is right.

    Suggestions are grouped per artist rather than listed one per row. Five candidate
    Deezer pages for one artist is *one* decision made five times, and a queue that shows
    it as five items makes an operator feel behind when they are not.
    """
    items: list[dict[str, Any]] = []

    rows = pending_suggestions(conn, tenant_id)
    by_party: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_party.setdefault(str(row["party_id"]), []).append(row)

    for party_id, group in by_party.items():
        name = group[0]["party_name"]
        best = max(g["confidence"] for g in group)
        platforms = sorted({(g["payload"] or {}).get("platform", "") for g in group} - {""})
        items.append({
            "id": f"sug-{party_id}",
            # An exact-looking match is a different act from a shortlist of maybes.
            "sev": "act" if best >= 0.7 else "warn",
            "icon": "◇", "kind": "Confirm",
            "head": f"{len(group)} candidate {'surface' if len(group) == 1 else 'surfaces'}"
                    f" for {name}",
            "sub": f"{', '.join(platforms) or 'search'} · best match {best:.2f}"
                   f" · found by search, not asserted",
            "cta": "Review", "href": f"/artists?sel={party_id}",
            "insp": (
                Section("Why this is here", "note", (
                    "An agent searched a source by name and found these. A name match is "
                    "inference, so none of them has been written as a surface — two acts "
                    "can share a name, and a wrong accept quietly attaches somebody "
                    "else's catalogue to your artist.",
                )),
                suggestions_section(group, back=f"/"),
                Section("", "actions", (("Open the artist", f"/artists?sel={party_id}", ""),)),
            ),
        })

    parked = _rows(conn, """
        SELECT l.id, l.kind, l.platform, l.last_error, l.attempts, p.name AS party_name
          FROM lead l LEFT JOIN party p ON p.tenant_id = l.tenant_id AND p.id = l.party_id
         WHERE l.tenant_id = %s AND l.state = 'failed'
         ORDER BY l.updated_at DESC LIMIT 20""", (tenant_id,))
    for row in parked:
        items.append({
            "id": f"lead-{row['id']}", "sev": "warn", "icon": "⚡", "kind": "Blocked",
            "head": f"{row['kind']} parked" + (f" for {row['party_name']}"
                                               if row["party_name"] else ""),
            "sub": f"{row['platform'] or 'no platform'} · {row['attempts']} attempts"
                   f" · {row['last_error'][:70]}",
            "cta": "Open", "href": "/queue",
            "insp": (
                Section("Why this is here", "note", (
                    "The lead failed enough times to be parked rather than retried. It is "
                    "here because the cause is something outside the fleet — a missing "
                    "credential, a disabled source — and no amount of backoff removes it.",
                )),
                Section("Lead", "kv", (
                    ("kind", row["kind"]), ("platform", row["platform"] or "—"),
                    ("attempts", str(row["attempts"])), ("error", row["last_error"][:200]),
                )),
            ),
        })

    counts = _one(conn, """
        SELECT (SELECT count(*) FROM lead WHERE tenant_id = %s AND state = 'pending') AS pending,
               (SELECT count(*) FROM party_chunk WHERE tenant_id = %s) AS chunks,
               (SELECT count(*) FROM party_fact WHERE tenant_id = %s AND status = 'live') AS facts,
               (SELECT count(*) FROM agent_run WHERE tenant_id = %s
                 AND started_at > now() - INTERVAL '24 hours') AS runs
    """, (tenant_id, tenant_id, tenant_id, tenant_id))
    quiet = (
        ("leads waiting", str(counts["pending"])),
        ("live facts", str(counts["facts"])),
        ("chunks indexed", str(counts["chunks"])),
        ("runs / 24h", str(counts["runs"])),
    )
    return items, quiet


# ------------------------------------------------------------------- artists

def artists(conn: psycopg.Connection, tenant_id: str, *,
            editing_id: str | None = None, error: str = "",
            profile_error: str = "", confirm_delete: bool = False) -> View:
    # The roster is a role, not a table. Joining `party_role` is what keeps a
    # counterparty — a creator, a curator, a journalist — out of this view while
    # letting it live in the same tables.
    rows = _rows(conn, """
        SELECT a.id, a.name, a.artist_type AS type, a.slug, a.kind, a.status,
               a.created_at,
               (SELECT count(*) FROM party_credit c
                 WHERE c.tenant_id = a.tenant_id AND c.party_id = a.id
                   AND c.subject_kind = 'recording')                          AS tracks,
               (SELECT count(*) FROM party_fact f
                 WHERE f.tenant_id = a.tenant_id AND f.party_id = a.id
                   AND f.status = 'live')                                     AS facts,
               (SELECT count(*) FROM lead l
                 WHERE l.tenant_id = a.tenant_id AND l.party_id = a.id
                   AND l.state = 'pending')                                   AS pending,
               (SELECT count(*) FROM presence p
                 WHERE p.tenant_id = a.tenant_id AND p.subject_kind = 'party'
                   AND p.subject_id = a.id AND p.mode <> 'absent')             AS profiles,
               (SELECT count(*) FROM party_document d
                 WHERE d.tenant_id = a.tenant_id AND d.party_id = a.id)        AS docs
          FROM party a
          JOIN party_role r ON r.tenant_id = a.tenant_id AND r.party_id = a.id
                            AND r.role = 'roster_artist'
         WHERE a.tenant_id = %s ORDER BY a.name""", (tenant_id,))

    out = []
    for r in rows:
        # `id` is needed because each surface is its own delete target, and the tenant
        # predicate is here rather than implied by the party — a scoped delete link
        # built from an unscoped read is how one label removes another's row.
        profiles = _rows(conn, """
            SELECT id, platform, mode, handle, url AS profile_url, state, match_basis
              FROM presence
             WHERE tenant_id = %s AND subject_kind = 'party' AND subject_id = %s
             ORDER BY platform""", (tenant_id, r["id"]))
        out.append({
            "id": str(r["id"]), "name": r["name"], "type": r["type"],
            "tracks": str(r["tracks"]), "facts": str(r["facts"]),
            "camps": str(r["pending"]), "streams": "—", "spark": "▁▁▁▁▁▁▁",
            "budget": f"{r['docs']}", "bar": _bar(min(100, int(r["docs"]) * 5)),
            "insp": artist_editor_sections(
                r, profiles,
                {"tracks": r["tracks"], "facts": r["facts"],
                 "docs": r["docs"], "pending": r["pending"]},
                # Only the row actually being edited carries the errors and the delete
                # confirmation; the other rows render their ordinary editor, so
                # selecting a different artist abandons a half-finished delete.
                error=(error if str(r["id"]) == editing_id else ""),
                profile_error=(profile_error if str(r["id"]) == editing_id else ""),
                confirm_delete=(confirm_delete and str(r["id"]) == editing_id),
                suggestions=pending_suggestions(conn, tenant_id, str(r["id"])),
            ),
        })

    return View(
        key="artists", title="Artists",
        blurb="The spine. Relationships, audience model and lessons accumulate here and "
              "are inherited by every release. Live from artist.",
        stats=(("roster", str(len(out)), ""),
               ("tracks", str(sum(int(r["tracks"]) for r in out)), ""),
               ("live facts", str(sum(int(r["facts"]) for r in out)), ""),
               ("documents", str(sum(int(r["budget"]) for r in out)), ""),
               ("pending leads", str(sum(int(r["camps"]) for r in out)), "")),
        cols=(Col("name", "Artist", "b", "22%"), Col("type", "Type", "chip", "12%"),
              Col("tracks", "Tracks", "num", "8%"), Col("facts", "Facts", "num", "8%"),
              Col("camps", "Leads", "num", "8%"), Col("budget", "Docs", "num", "8%"),
              Col("bar", "", "bar", "14%"), Col("streams", "30d", "num", "10%"),
              Col("spark", "", "spark", "10%")),
        rows=tuple(out),
        empty="No artists yet — add one from the roster form.",
    )
