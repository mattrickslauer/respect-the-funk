"""Console views over the real tables.

The promise `demo.py` made was that when a table landed, the fixture would become a
`repo` call and the templates would not change. This is that call. Every function here
returns a `demo.View` — same columns, same chips, same inspector sections — so the
switch is a route edit and nothing below it moves.

All thirteen are real now. The last five — fleet, campaigns, threads, approvals and
inbox — arrived with migration 010, which built the outreach tables `PLATFORM-SPEC §2d`
specified. There are no fixtures left anywhere in the console.

That does not mean every agent behind a view exists. The distinction the nav badge used
to draw between "live" and "wireframe" now has to be drawn inside each view instead, and
it is: `/fleet` marks an agent `declared` when it has a manifest and no implementation,
`/approvals` says the send it prepares is claimed by nobody, and `/inbox` says its table
has no adapter writing into it. A screen that is honest about its own gaps is worth more
than one that is uniformly green.

**Empty is a real answer here.** A view with no rows says what would create some,
because an empty table and a broken query look identical otherwise, and the operator
should be able to tell without opening a log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

from rtf_platform.demo import Col, Field, Section, View, _bar
from rtf_platform.domain import (
    ARTIST_STATUSES, CREDIT_ROLES, DEFAULT_TYPE, RECORDING_STATUSES, ArtistType,
    Platform, ProfileMode, unrecognised,
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
                # Run now posts; the other three are still the inert shape, and they
                # look inert on purpose until something exists to post them to. What
                # Run now does is bring `next_action_at` forward — see `fleet.expedite`
                # for why that is the only thing it can honestly mean here.
                Section("", "actions", (
                    ("Run now", f"/queue/{r['id']}/run", "p", "post"),
                    "Release lease", "Reject", "Boost",
                )),
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

from rtf_platform import settings as settings_mod


def _mb(value: Any) -> str:
    """Bytes as megabytes, or an em dash. NULL is a real answer here — see
    `recording_asset.bytes` — and rendering it as 0 MB would claim an empty file."""
    if not value:
        return "—"
    return f"{int(value) / (1024 * 1024):.1f} MB"


def _audio_cell(assets: list[dict[str, Any]]) -> str:
    """One word for "can anything listen to this track yet".

    `master` is what the classifier and every `SCOPE-RESET §2` measurement need; a
    stored instrumental is better than nothing but cannot answer the same questions, so
    it is named rather than counted as audio. `pending` is deliberately visible: a
    signed-but-never-finished upload should look different from no upload at all,
    because the fix for the two is different.
    """
    stored = {a["kind"] for a in assets if a["state"] == "stored"}
    if "master" in stored:
        return "master"
    if stored:
        return sorted(stored)[0]
    if any(a["state"] == "pending" for a in assets):
        return "pending"
    return "none"


def _masters_sections(recording_id: str, assets: list[dict[str, Any]],
                      measured: dict[str, dict[str, Any]] | None = None,
                      ) -> tuple[Section, ...]:
    """The masters panel: what is held, and the form that adds to it.

    The form is only offered when there is somewhere for it to put a file. A console
    that renders an upload control against an unconfigured bucket is worse than one that
    says the feature is off — the operator spends a 90MB upload finding out.
    """
    from rtf_platform import assets as assets_mod

    sections: list[Section] = []

    if assets:
        sections.append(Section("Audio held", "editlist", tuple(
            (
                a["kind"] + (f" · {a['label']}" if a["label"] else ""),
                " · ".join(filter(None, (
                    _mb(a["bytes"]),
                    a["state"] if a["state"] != "stored" else "",
                    _ago(a, "uploaded_at") if a["uploaded_at"] else "not uploaded",
                    f"{a['sample_rate']} Hz" if a["sample_rate"] else "",
                ))),
                f"/tracks/{recording_id}/masters/{a['id']}/delete",
            )
            for a in assets)))

    measured = measured or {}
    has_master = any(a["kind"] == "master" and a["state"] == "stored" for a in assets)

    # A master that has been listened to and still has no genre means one of two very
    # different things, and a track showing neither sends an operator hunting for a bug
    # in the upload. Say which.
    if has_master and not measured.get("genre"):
        if not settings_mod.load().classifier_configured:
            sections.append(Section("No genre: no classifier", "note", (
                "PLATFORM_CLASSIFIER_FUNCTION is unset, so analyse_recording measures "
                "tempo and deliberately writes no genre rather than passing "
                "tempo-band guesses off as a model's answer.",
            )))
        elif measured:
            sections.append(Section("No genre: nothing recognised", "note", (
                "The classifier ran and nothing cleared its confidence floor. A 400-way "
                "sigmoid always has a best answer; reporting one this weak would be "
                "laundering noise into a label.",
            )))

    if not has_master:
        sections.append(Section("No master yet", "note", (
            "Every measured fact about a recording — key, sections, energy, the hook "
            "window — needs the whole file. A 30-second preview carries tempo and "
            "almost nothing else, which is why `audio.py` refuses to report the rest.",
        )))

    if not settings_mod.load().storage_configured:
        sections.append(Section("Uploads are not configured", "note", (
            "PLATFORM_MASTERS_BUCKET is unset, so there is nowhere to put a master. "
            "Run terraform apply in platform/infra — it creates the bucket and sets "
            "the variable on the deployed console.",
        )))
        return tuple(sections)

    sections.append(Section(
        "Upload", "upload",
        items=tuple((kind, kind.replace("_", " ")) for kind in assets_mod.UPLOADABLE_KINDS),
        action=f"/tracks/{recording_id}/masters",
        submit="Upload",
        note=f"Up to {assets_mod.MAX_ASSET_BYTES // (1024 * 1024)} MB. WAV, AIFF, "
             f"FLAC, MP3, M4A, AAC or OGG. The file goes from this page straight to "
             f"S3 — it does not pass through the console.",
    ))
    return tuple(sections)


#: The dimensions `analyse_recording` writes, in the order a person reads them. Kept
#: here rather than imported from `agents._AUDIO_DIMENSIONS` because that tuple is the
#: *writer's* list and this is the *reader's*: it also carries `genre`, `style` and
#: `style_terms`, which are written further down that same function and not from it.
#:
#: There is no `key` in this list, and that is not an omission. Nothing in `audio.py`
#: estimates a musical key, so the column the table used to reserve for one could never
#: hold anything but an em dash — see the note on `tracks`'s columns.
_MEASURED_DIMENSIONS: tuple[str, ...] = (
    "bpm", "bpm_confidence", "brightness_hz", "genre", "style", "style_terms",
)


def _measurements(conn: psycopg.Connection,
                  tenant_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    """The live measured facts for every recording, keyed `{recording: {dimension: row}}`.

    One query for the tenant rather than one per row, the same rule the asset fetch
    follows: the table is the unit of work here, not the record.

    `DISTINCT ON` rather than a bare `status = 'live'` filter because live is not quite
    unique. `agents._supersede_recording_fact` supersedes within
    `(recording, dimension, provenance)`, so a dimension written once as `measured` and
    later as `inferred` leaves two live rows, and picking arbitrarily between them would
    make the column flicker between page loads. Newest wins, deterministically.
    """
    rows = _rows(conn, """
        SELECT DISTINCT ON (recording_id, dimension)
               id, recording_id, dimension, value_text, provenance, source, observed_at
          FROM party_fact
         WHERE tenant_id = %s AND recording_id IS NOT NULL AND status = 'live'
           AND dimension = ANY(%s)
         ORDER BY recording_id, dimension, observed_at DESC""",
        (tenant_id, list(_MEASURED_DIMENSIONS)))

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row["recording_id"]), {})[row["dimension"]] = row
    return out


def _value(measured: dict[str, dict[str, Any]], dimension: str, default: str = "—") -> str:
    row = measured.get(dimension)
    return row["value_text"] if row and row["value_text"] else default


def _confidence_bar(measured: dict[str, dict[str, Any]]) -> str:
    """The tempo confidence as a bar, or blank.

    Blank rather than an empty bar when nothing has been measured: a ten-cell trough
    reads as "measured, and scored zero", which is the opposite of "not listened to yet".
    """
    raw = _value(measured, "bpm_confidence", "")
    if not raw:
        return ""
    try:
        return _bar(int(round(float(raw) * 100)))
    except ValueError:
        # A non-numeric confidence is a writer bug, not something to render as 0%.
        return ""


def _measured_section(measured: dict[str, dict[str, Any]],
                      assets: list[dict[str, Any]]) -> Section:
    """What listening to the master established, and what it was listening to.

    Empty is a real answer and says which of its two causes it is — no master at all, or
    a master nothing has got to yet — because the fix for the two is different.
    """
    if not measured:
        stored = any(a["state"] == "stored" for a in assets)
        return Section("Measured", "note", (
            "Nothing measured yet. The master is stored, so `analyse_recording` has "
            "either not claimed it or has not finished — check the queue."
            if stored else
            "Nothing measured yet, and no master is stored. Upload one and the "
            "analysis is queued from the upload; no other action is needed.",
        ))

    shaped = next((a for a in assets if a.get("duration_ms") or a.get("sample_rate")),
                  None)
    items: list[tuple[str, str]] = []

    bpm = _value(measured, "bpm", "")
    if bpm:
        confidence = _value(measured, "bpm_confidence", "")
        items.append(("bpm", f"{bpm} · {confidence} confidence" if confidence else bpm))

    brightness = _value(measured, "brightness_hz", "")
    if brightness:
        # Spelled out because "brightness" is not a word anyone outside this codebase
        # uses for a spectral centroid, and the number is meaningless without the unit.
        items.append(("brightness", f"{brightness} Hz (spectral centroid)"))

    for dimension, label in (("genre", "genre"), ("style", "style"),
                             ("style_terms", "style terms")):
        value = _value(measured, dimension, "")
        if not value:
            continue
        # `style` is stored as the full Discogs label — "Electronic---Tropical House".
        # The parent is already on the line above as `genre`, so the tail is the part
        # that carries new information; the separator is an artefact of the taxonomy
        # and not something an operator should have to read past.
        if dimension == "style":
            value = value.split("---")[-1]
        items.append((label, value))

    if shaped:
        if shaped.get("duration_ms"):
            seconds = int(shaped["duration_ms"]) // 1000
            items.append(("duration", f"{seconds // 60}:{seconds % 60:02d}"))
        if shaped.get("sample_rate"):
            items.append(("sample rate", f"{shaped['sample_rate']} Hz"))

    # Which measurement this is *of*. A number taken off a 30-second preview is a
    # different claim from one taken off the master, and `audio.py` names the difference
    # in `source` precisely so a reader can tell without opening the fact row.
    source = next((r["source"] for r in measured.values() if r.get("source")), "")
    if source:
        items.append(("listened to", source))
    items.append(("measured", _ago(next(iter(measured.values())), "observed_at")))

    return Section("Measured", "kv", tuple(items))


def _provenance_section(conn: psycopg.Connection,
                        measured: dict[str, dict[str, Any]]) -> tuple[Section, ...]:
    """The basis walk for the tempo fact, which is the walk for all of them.

    Every fact `analyse_recording` writes hangs off the same `recording_asset` edge, so
    rendering one walk per dimension would repeat the same single row six times. The
    tempo fact is the representative because it is the one always present when anything
    is.

    `"fact"` is the subject kind because that is the literal `agents` writes into
    `fact_basis`. It is worth stating out loud: `facts()` above walks `"party_fact"` for
    the same edges and therefore finds none of them.
    """
    anchor = measured.get("bpm") or next(iter(measured.values()), None)
    if anchor is None:
        return ()
    walk = _basis(conn, "fact", anchor["id"])
    if walk and walk[0][1] == "nothing":
        return ()
    return (Section("Measured from", "chain", tuple(walk)),)


def _credit_sections(recording_id: str, credits: list[dict[str, Any]],
                     roster: list[dict[str, Any]], *,
                     error: str = "") -> tuple[Section, ...]:
    """Who is on the record, and the form that adds someone.

    Credits get the same `editlist` shape as the artist inspector's surfaces because
    they are the same kind of thing: a row an operator asserts, removable one at a time,
    where removing the wrong one is silent. It is not cosmetic here — the main-artist
    credit is what `assets.queue_analysis` scopes an analysis lead by, so deleting it
    stops the track being analysable at all.
    """
    listed = Section(
        "Credits", "editlist",
        tuple(
            (c["party_name"],
             f"{c['role'].replace('_', ' ')} · {c['provenance']}",
             f"/tracks/{recording_id}/credits/{c['id']}/delete")
            for c in credits
        ) or ((None, "Nobody is credited — so this track belongs to no artist, and "
                     "nothing can be analysed or budgeted against it.", None),),
    )

    if not roster:
        return (listed, Section("No artists yet", "note", (
            "A credit points at a party, and there are none. Add an artist first.",
        )))

    add = Section(
        "Credit an artist", "form", (
            Field("party_id", "Artist", "select", "",
                  _flat(tuple((str(a["id"]), a["name"]) for a in roster))),
            Field("role", "Role", "select", "main_artist",
                  _flat(tuple((r, r.replace("_", " ")) for r in CREDIT_ROLES)),
                  hint="Main artist and featured are the two that decide ownership."),
        ),
        action=f"/tracks/{recording_id}/credits", submit="Add credit", error=error,
    )
    return (listed, add)


def _recording_fields(row: dict[str, Any]) -> tuple[Field, ...]:
    return (
        Field("title", "Title", "text", row.get("title", ""), required=True),
        Field("isrc", "ISRC", "text", row.get("isrc_raw") or row.get("isrc", ""),
              placeholder="QZ-ABC-25-00001",
              hint="Stored canonically. A malformed one is refused, not filed."),
        Field("released_on", "Released", "date",
              str(row["released_on"]) if row.get("released_on") else "",
              placeholder="YYYY-MM-DD"),
    )


def new_recording_sections(*, form: dict[str, str] | None = None,
                           error: str = "") -> tuple[Section, ...]:
    """The inspector when `?sel=new`. Same fields as the editor, minus status — a
    recording nobody has saved cannot be archived."""
    form = form or {}
    return (
        Section("New track", "form", _recording_fields(form),
                action="/tracks", submit="Add track", error=error),
        Section("", "actions", (("Cancel", "/tracks", ""),)),
        Section("Note", "note", (
            "Most recordings arrive from a statement import or a source harvest, with "
            "their ISRC already attached. This is the hand path — for a master you are "
            "holding before anything has reported it.",
        )),
    )


def recording_editor_sections(
    conn: psycopg.Connection, tenant_id: str, row: dict[str, Any],
    assets: list[dict[str, Any]], measured: dict[str, dict[str, Any]],
    credits: list[dict[str, Any]], roster: list[dict[str, Any]], *,
    error: str = "", credit_error: str = "", confirm_delete: bool = False,
) -> tuple[Section, ...]:
    """Everything the inspector shows for one recording: read it, act on it, edit it.

    This used to be a key-value block, the masters panel, and three buttons that posted
    nowhere — `("Analyse", "Seed frontier", "Edit")` rendered as controls because the
    template draws a bare string as an inert button. The artist inspector solved the
    same problem by absorbing the editor that lived at `/roster`; this is that move.
    """
    rid = str(row["id"])
    has_master = any(a["kind"] == "master" and a["state"] == "stored" for a in assets)
    credited = any(c["role"] in ("main_artist", "featured") for c in credits)

    edit = Section(
        "Recording", "form",
        _recording_fields(row) + (
            Field("status", "Status", "select", row["status"],
                  _flat(tuple((s, s) for s in RECORDING_STATUSES)),
                  hint="Paused keeps the history and stops the agents."),
        ),
        action=f"/tracks/{rid}", submit="Save", error=error,
    )

    record = Section("Record", "kv", (
        ("slug", row["slug"]),
        ("isrc", row["isrc"] or "—"),
        ("added", row["created_at"].strftime("%Y-%m-%d")),
        ("live facts", str(row["facts"])),
        ("pending leads", str(row["leads"])),
        ("platforms", str(row["places"])),
    ))

    #: Only the buttons that do something. "Analyse" posts; the rest navigate. The two
    #: reasons an analysis cannot be queued are stated before it is offered rather than
    #: discovered by pressing it, because `queue_analysis` returning False is silent.
    controls: list[Any] = []
    if has_master and credited:
        controls.append(("Analyse again", f"/tracks/{rid}/analyse", "p", "post"))
    artist = next((c for c in credits if c["role"] in ("main_artist", "featured")), None)
    if artist:
        controls.append(("Artist", f"/artists?sel={artist['party_id']}", ""))
    controls.append(("Facts", "/facts", ""))
    controls.append(("Queue", "/queue", ""))
    actions = Section("", "actions", tuple(controls))

    blocked: tuple[Section, ...] = ()
    if not has_master:
        blocked = (Section("Analysis is not available", "note", (
            "No master is stored, so there is nothing to listen to. Uploading one "
            "queues the analysis by itself — see below.",
        )),)
    elif not credited:
        blocked = (Section("Analysis is not available", "note", (
            "Nobody is credited as main artist or featured. `lead_scope_shape` requires "
            "a recording-scoped lead to carry a party — analysis is spent against the "
            "artist whose record it is — so nothing can be queued until a credit exists.",
        )),)

    note = Section("Note", "note", (
        "Re-analysing is not free and not always a no-op: a fact is superseded rather "
        "than overwritten, so the previous measurement stays readable and "
        "\"why did we think it was 84 BPM in March\" keeps its answer.",
    ))

    body = (
        edit, record,
        _measured_section(measured, assets),
        *_provenance_section(conn, measured),
        *_masters_sections(rid, assets, measured),
        *_credit_sections(rid, credits, roster, error=credit_error),
        *blocked, actions, note,
    )

    if confirm_delete:
        danger = Section(
            "Delete", "form", (
                Field("", "", "static",
                      f"Deleting {row['title']} also deletes {row['facts']} facts, "
                      f"{len(credits)} credits, {row['places']} platform rows, "
                      f"{row['leads']} pending leads and {len(assets)} stored file(s) — "
                      "including the master, from the bucket. This cannot be undone."),
            ),
            action=f"/tracks/{rid}/delete", submit="Yes, delete permanently",
            tone="danger",
        )
        # First, not last, for the reason the artist editor gives: the control that asks
        # for the confirmation sits at the bottom of a scrolling pane, so a confirmation
        # rendered in place would appear below the fold and read as a click that did
        # nothing.
        return (danger, Section("", "actions", (("Cancel", f"/tracks?sel={rid}", ""),)),
                *body)

    return (*body, Section("", "actions", (
        ("Delete track", f"/tracks?sel={rid}&confirm=delete", "d"),
    )))


def tracks(conn: psycopg.Connection, tenant_id: str, *,
           editing_id: str | None = None, error: str = "",
           credit_error: str = "", confirm_delete: bool = False) -> View:
    # A recording is not owned by an artist — credits are, which is what lets one
    # recording carry two main artists instead of being stored twice. So the
    # performer comes from `party_credit`, and `string_agg` because there can be
    # more than one and picking the first would quietly hide the collaborator.
    #
    # `t.tenant_id` is in the `GROUP BY` because the three scalar subqueries correlate
    # on it, and a subquery's reference to an outer column does not get the
    # group-by-the-primary-key relaxation that the select list gets — grouping by
    # `t.id` is not enough, and the planner rejects the statement outright
    # (`subquery uses ungrouped column "tenant_id" from outer query`). It changes no
    # result: `t.id` is already unique, so the extra column splits no group. Do not
    # drop it as redundant without deleting the `f.tenant_id = t.tenant_id` predicates
    # too, and those are load-bearing — see `tests/test_tenant_scoping.py`.
    rows = _rows(conn, """
        SELECT t.id, t.title, t.slug, t.isrc, t.isrc_raw, t.released_on, t.status,
               t.created_at,
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
         GROUP BY t.tenant_id, t.id, t.title, t.slug, t.isrc, t.isrc_raw,
                  t.released_on, t.status, t.created_at
         ORDER BY t.title""", (tenant_id,))

    # Every asset for the tenant in one query, grouped in Python, rather than one query
    # per row. Thirteen views render the same way and none of them fans out per row —
    # the table is the unit of work here, not the record.
    by_recording: dict[str, list[dict[str, Any]]] = {}
    for asset in _rows(conn, """
            SELECT id, recording_id, kind, label, bytes, mime, state, uploaded_at,
                   uploaded_by, duration_ms, sample_rate
              FROM recording_asset
             WHERE tenant_id = %s
             ORDER BY kind, created_at DESC""", (tenant_id,)):
        by_recording.setdefault(str(asset["recording_id"]), []).append(asset)

    # The measured facts and the credits, each in one query for the whole tenant, for
    # the reason the asset fetch above gives.
    measurements = _measurements(conn, tenant_id)

    by_credit: dict[str, list[dict[str, Any]]] = {}
    for credit in _rows(conn, """
            SELECT c.id, c.subject_id, c.party_id, c.role, c.provenance,
                   p.name AS party_name
              FROM party_credit c
              JOIN party p ON p.tenant_id = c.tenant_id AND p.id = c.party_id
             WHERE c.tenant_id = %s AND c.subject_kind = 'recording'
             ORDER BY c.role, p.name""", (tenant_id,)):
        by_credit.setdefault(str(credit["subject_id"]), []).append(credit)

    # Fetched once and shared by every row's credit form rather than per selection: the
    # inspector is built for all rows here, and the roster does not vary between them.
    roster = _rows(conn, """
        SELECT a.id, a.name FROM party a
          JOIN party_role r ON r.tenant_id = a.tenant_id AND r.party_id = a.id
                            AND r.role = 'roster_artist'
         WHERE a.tenant_id = %s ORDER BY a.name""", (tenant_id,))

    out = []
    for r in rows:
        rid = str(r["id"])
        assets = by_recording.get(rid, [])
        measured = measurements.get(rid, {})
        out.append({
            "id": rid, "title": r["title"], "artist": r["artist_name"],
            "state": r["status"],
            "audio": _audio_cell(assets),
            "bpm": _value(measured, "bpm"),
            # The style when the classifier resolved one, the parent genre otherwise.
            # The style is the more specific answer and is only written above the
            # model's own confidence floor, so preferring it costs nothing when absent.
            # Split on the Discogs separator: the column has room for "House", not for
            # "Electronic---House".
            "genre": (_value(measured, "style", "").split("---")[-1]
                      or _value(measured, "genre")),
            "campaigns": str(r["leads"]), "streams": str(r["places"]),
            "conf": _confidence_bar(measured),
            "insp": recording_editor_sections(
                conn, tenant_id, r, assets, measured,
                by_credit.get(rid, []), roster,
                error=(error if editing_id == rid else ""),
                credit_error=(credit_error if editing_id == rid else ""),
                confirm_delete=(confirm_delete and editing_id == rid),
            ),
        })

    with_master = sum(1 for r in out if r["audio"] == "master")
    analysed = sum(1 for r in out if r["bpm"] != "—")
    return View(
        key="tracks", title="Tracks",
        blurb="Recordings — the masters, identified by ISRC. Credits decide who they "
              "belong to, so a collaboration is one row. Live from recording, and "
              "editable in place.",
        stats=(("tracks", str(len(out)), ""),
               ("with a master", str(with_master), ""),
               ("analysed", str(analysed), _bar(_pct(analysed, len(out) or 1))),
               ("with leads", str(sum(1 for r in out if r["campaigns"] != "0")), ""),
               ("facts", str(sum(int(r["facts"]) for r in rows)), "")),
        # No `Key` column. Nothing in `audio.py` estimates a musical key, so the column
        # that used to sit here was a hardcoded em dash in every row of every tenant — a
        # promise of a measurement this platform has never been able to take. `Genre` is
        # in its place because the classifier genuinely writes one.
        cols=(Col("title", "Track", "b", "22%"), Col("artist", "Artist", "", "16%"),
              Col("state", "Status", "chip", "10%"), Col("audio", "Audio", "chip", "10%"),
              Col("bpm", "BPM", "num", "7%"), Col("genre", "Genre", "", "13%"),
              Col("campaigns", "Leads", "num", "6%"), Col("streams", "On", "num", "6%"),
              Col("conf", "", "bar", "10%")),
        rows=tuple(out),
        empty="No recordings yet. One arrives with an ISRC, and the ISRC is what "
              "places it on every platform — or start one by hand with ＋ New track.",
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


# ===========================================================================
# Outreach — the four views migration 010 turned real, and the fleet.
#
# These were the last fixtures. `demo.py` promised that when the tables landed the
# fixture would become a query and the templates would not change, and that is what
# happened here: not one line of `console/table.html`, `approvals.html` or `inbox.html`
# moved to accommodate them.
#
# The numbers are small and real. A fixture showed 48 open threads because a dense
# screen is easier to judge; these show what is actually in the cluster, which today is
# nearly nothing. That is the correct reading and the empty states say what would
# change it.
# ===========================================================================

def _since(value: Any) -> str:
    """Coarse relative age — `4m`, `3h`, `2d`.

    Coarse on purpose. The operator question these answer is "is this stale", and a
    thread that has been waiting eleven hours and one that has been waiting twelve
    demand the same action, so the extra precision would be noise dressed as detail.
    """
    if value is None:
        return "—"
    delta = datetime.now(timezone.utc) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


# ---------------------------------------------------------------------- fleet

#: What each state means on the fleet screen, kept next to the code that assigns it
#: because "idle" and "off" look similar in a table and mean opposite things — one is
#: waiting for work and one will never take any.
_FLEET_STATE_NOTE = {
    "working":   "holding a lease right now",
    "idle":      "enabled, nothing claimed",
    "off":       "disabled — an UPDATE, not a deploy",
    "declared":  "a manifest with no implementation behind it",
}


def fleet(conn: psycopg.Connection, tenant_id: str) -> View:
    """The fleet, from `agent_manifest` cross-referenced against the running code.

    Three facts are joined here and each is worth being able to see separately:

      * what the database says should exist — `agent_manifest`
      * what the deployed code can actually run — `agents.REGISTRY`
      * what has actually happened — `agent_run` and the live leases on `lead`

    The second is the one a fixture could never show. An agent row with `enabled = true`
    and no implementation is not an idle agent, it is a promise; it renders as `declared`
    so the two cannot be confused. This is the same reason `/artists` was built as live
    rows with wireframe columns — showing where the substrate stops beats hiding it.

    **Runs are attributed to the worker that claimed the lead, not to the agent function
    it dispatched to.** Every `agent_run` row on this cluster today says `ingest-cli`,
    because the CLI claims as one worker and then calls whichever agent the lead's kind
    selects. So a manifest row can legitimately show zero runs while its code has run
    many times. Rather than silently dropping those rows, any `agent_kind` with runs and
    no manifest is listed too, marked `unmanifested`.
    """
    # Deliberately imported here rather than at module scope: `agents` imports the
    # embedding adapters, and a console page should not fail to render because an
    # optional provider SDK is missing from the bundle.
    try:
        from rtf_platform.agents import REGISTRY
        implemented = set(REGISTRY)
    except Exception:  # noqa: BLE001 — an unimportable registry is a fact to display
        implemented = set()

    manifests = _rows(conn, """
        SELECT kind, work_table, claim_state, scope_kinds, adapters, writes,
               batch_size, per_artist_cap, lease_seconds, max_attempts,
               requires_human, enabled, updated_at
          FROM agent_manifest
         ORDER BY enabled DESC, kind""", ())

    runs = {r["agent_kind"]: r for r in _rows(conn, """
        SELECT agent_kind,
               count(*) AS total,
               count(*) FILTER (WHERE started_at > now() - INTERVAL '1 hour') AS last_hour,
               count(*) FILTER (WHERE state = 'failed') AS errors,
               count(*) FILTER (WHERE state = 'refused') AS refused,
               max(started_at) AS last_run,
               sum(cost_micro_usd) AS cost_micro
          FROM agent_run
         WHERE tenant_id = %s
         GROUP BY agent_kind""", (tenant_id,))}

    leases = {r["owner_agent"]: r["n"] for r in _rows(conn, """
        SELECT owner_agent, count(*) AS n
          FROM lead
         WHERE tenant_id = %s AND owner_agent IS NOT NULL
           AND lease_expires_at > now()
         GROUP BY owner_agent""", (tenant_id,))}

    # Work waiting per table, so "idle" can be read as "idle with nothing to do" rather
    # than "idle with a backlog", which are very different bugs.
    waiting = {
        "lead": _one(conn, "SELECT count(*) AS n FROM lead WHERE tenant_id = %s "
                           "AND state = 'pending' AND next_action_at <= now()",
                     (tenant_id,)).get("n", 0),
        "thread": _one(conn, "SELECT count(*) AS n FROM thread WHERE tenant_id = %s "
                             "AND state = 'approved'", (tenant_id,)).get("n", 0),
        "outbox": _one(conn, "SELECT count(*) AS n FROM outbox WHERE tenant_id = %s "
                             "AND state = 'pending'", (tenant_id,)).get("n", 0),
        "message": _one(conn, "SELECT count(*) AS n FROM message WHERE tenant_id = %s "
                              "AND direction = 'inbound' AND intent = ''",
                        (tenant_id,)).get("n", 0),
    }

    out: list[dict[str, Any]] = []
    for m in manifests:
        kind = m["kind"]
        run = runs.get(kind, {})
        held = leases.get(kind, 0)
        has_code = kind in implemented
        queued = waiting.get(m["work_table"], 0)

        if not m["enabled"]:
            state = "off"
        elif not has_code:
            state = "declared"
        elif held:
            state = "working"
        else:
            state = "idle"

        errors = int(run.get("errors") or 0)
        total = int(run.get("total") or 0)
        cost = Decimal(int(run.get("cost_micro") or 0)) / Decimal(1_000_000)

        out.append({
            "id": kind, "agent": kind, "state": state, "leased": str(held),
            # The bar reads as "how much of this agent's batch is in flight", which is
            # the only ratio on this row that is bounded and therefore meaningful.
            "bar": _bar(_pct(held, m["batch_size"] or 1)),
            "work": m["work_table"], "scope": ",".join(m["scope_kinds"] or []) or "—",
            "rate": str(int(run.get("last_hour") or 0)),
            "spark": "▁▁▁▁▁▁▁", "errors": str(errors),
            "insp": (
                Section("Manifest", "kv", (
                    ("kind", kind), ("work_table", m["work_table"]),
                    ("claim_state", m["claim_state"]),
                    ("scope_kinds", ", ".join(m["scope_kinds"] or []) or "—"),
                    ("adapters", ", ".join(m["adapters"] or []) or "none"),
                    ("writes", ", ".join(m["writes"] or []) or "nothing"),
                    ("batch_size", str(m["batch_size"])),
                    ("per_artist_cap", str(m["per_artist_cap"])),
                    ("lease_seconds", str(m["lease_seconds"])),
                    ("max_attempts", str(m["max_attempts"])),
                    ("requires_human", "true" if m["requires_human"] else "false"),
                    ("enabled", "true" if m["enabled"] else "false"),
                )),
                Section("Right now", "kv", (
                    ("state", f"{state} — {_FLEET_STATE_NOTE[state]}"),
                    ("implementation", "in agents.REGISTRY" if has_code
                     else "none — the manifest is ahead of the code"),
                    ("leases held", str(held)),
                    ("work waiting in " + m["work_table"], str(queued)),
                    ("runs recorded", str(total)),
                    ("runs last hour", str(int(run.get("last_hour") or 0))),
                    ("failed", str(errors)),
                    ("refused by the gate", str(int(run.get("refused") or 0))),
                    ("last run", _since(run.get("last_run"))),
                    ("est. cost to date", f"${cost:.4f}"),
                )),
                Section("Attribution", "note", (
                    "Runs are recorded against the worker that claimed the lead, and the CLI "
                    "claims as one worker for every agent kind. So zero runs here means zero "
                    "runs under this name — not that the code has never executed. The "
                    "unmanifested rows below the fold are where those runs actually landed.",
                )) if total == 0 and has_code else Section("", "note", ()),
                # Turning an agent off is an UPDATE, not a deploy — which combined with
                # the lease is a clean drain: it stops claiming, finishes what it holds,
                # and goes quiet. That is why this is one button and not a release.
                Section("", "actions", (
                    (("Disable", f"/fleet/{kind}/toggle", "d", "post") if m["enabled"]
                     else ("Enable", f"/fleet/{kind}/toggle", "p", "post")),
                    ("Runs", "/runs", ""),
                    ("Queue", "/queue", ""),
                )),
            ),
        })

    # Anything that has run but has no manifest. Listed rather than dropped: a worker
    # nobody declared is exactly the thing you want to find out about from the screen
    # that claims to show the fleet.
    for kind, run in sorted(runs.items()):
        if any(m["kind"] == kind for m in manifests):
            continue
        errors = int(run.get("errors") or 0)
        cost = Decimal(int(run.get("cost_micro") or 0)) / Decimal(1_000_000)
        out.append({
            "id": kind, "agent": kind, "state": "unmanifested", "leased": "0",
            "bar": _bar(0), "work": "—", "scope": "—",
            "rate": str(int(run.get("last_hour") or 0)),
            "spark": "▁▁▁▁▁▁▁", "errors": str(errors),
            "insp": (
                Section("Unmanifested worker", "kv", (
                    ("name", kind), ("runs recorded", str(int(run.get("total") or 0))),
                    ("failed", str(errors)),
                    ("refused by the gate", str(int(run.get("refused") or 0))),
                    ("last run", _since(run.get("last_run"))),
                    ("est. cost to date", f"${cost:.4f}"),
                )),
                Section("Note", "note", (
                    "This name has agent_run rows and no row in agent_manifest, so nothing "
                    "describes what it claims, what it may write or whether it is allowed to "
                    "run. `ingest-cli` is the expected one — it is the CLI worker, which "
                    "claims leads under its own name and dispatches to whichever agent the "
                    "lead's kind selects. A name here that is not the CLI is worth chasing.",
                )),
                Section("", "actions", (("Runs", "/runs", ""),)),
            ),
        })

    enabled = sum(1 for r in out if r["state"] in ("working", "idle"))
    return View(
        key="fleet", title="Fleet",
        blurb="No orchestrator. Each agent claims work by lease, acts, writes back, and "
              "that write is what wakes the next one. Live from agent_manifest, "
              "cross-referenced against the code that is actually deployed.",
        stats=(("declared", str(len(manifests)), ""),
               ("runnable", str(enabled), _bar(_pct(enabled, len(manifests) or 1))),
               ("working", str(sum(1 for r in out if r["state"] == "working")), ""),
               ("off", str(sum(1 for r in out if r["state"] == "off")), ""),
               ("leads waiting", str(waiting["lead"]), "")),
        cols=(Col("agent", "Agent", "b", "16%"), Col("state", "State", "chip", "12%"),
              Col("leased", "Leased", "num", "7%"), Col("bar", "", "bar", "11%"),
              Col("work", "Claims from", "mono", "14%"),
              Col("scope", "Scope", "mono", "16%"), Col("rate", "1h", "num", "7%"),
              Col("errors", "Err", "num", "6%")),
        rows=tuple(out),
        empty="No agents declared. Migration 010 seeds the manifest.",
    )


# ------------------------------------------------------------------ campaigns

def campaigns(conn: psycopg.Connection, tenant_id: str) -> View:
    """One track, one channel, one goal — and the funnel underneath it.

    Every count here is derived from `thread` and `message` rather than stored on the
    campaign. A denormalised counter would need maintaining in every transition and
    would be wrong the first time one was missed, and this table is small enough that
    counting is free. When it is not, the fix is a materialised view, not a column
    somebody has to remember to increment.
    """
    rows = _rows(conn, """
        SELECT c.id, c.name, c.channel, c.state, c.goal,
               c.started_at, c.created_at,
               p.name AS artist,
               r.title AS track,
               (SELECT count(*) FROM thread t
                 WHERE t.tenant_id = c.tenant_id AND t.campaign_id = c.id) AS threads,
               (SELECT count(*) FROM thread t
                 WHERE t.tenant_id = c.tenant_id AND t.campaign_id = c.id
                 AND t.state NOT IN ('closed_won','closed_lost','closed_no_reply')) AS open_threads,
               (SELECT count(*) FROM thread t
                 WHERE t.tenant_id = c.tenant_id AND t.campaign_id = c.id
                 AND t.state = 'awaiting_human') AS awaiting,
               (SELECT count(*) FROM message m
                  JOIN thread t ON t.tenant_id = m.tenant_id AND t.id = m.thread_id
                 WHERE m.tenant_id = c.tenant_id AND t.campaign_id = c.id
                   AND m.direction = 'outbound'
                   AND m.sent_at IS NOT NULL) AS sent,
               (SELECT count(*) FROM outbox o
                  JOIN thread t ON t.tenant_id = o.tenant_id AND t.id = o.thread_id
                 WHERE o.tenant_id = c.tenant_id AND t.campaign_id = c.id
                   AND o.state = 'pending') AS queued,
               (SELECT count(*) FROM message m
                  JOIN thread t ON t.tenant_id = m.tenant_id AND t.id = m.thread_id
                 WHERE m.tenant_id = c.tenant_id AND t.campaign_id = c.id
                   AND m.direction = 'inbound') AS replied,
               (SELECT count(*) FROM thread t
                 WHERE t.tenant_id = c.tenant_id AND t.campaign_id = c.id
                 AND t.state IN ('agreed','delivered','verified','closed_won')) AS agreed,
               (SELECT count(*) FROM thread t
                 WHERE t.tenant_id = c.tenant_id AND t.campaign_id = c.id
                 AND t.state IN ('delivered','verified','closed_won')) AS delivered
          FROM campaign c
          JOIN party p ON p.tenant_id = c.tenant_id AND p.id = c.party_id
          LEFT JOIN recording r ON r.tenant_id = c.tenant_id AND r.id = c.recording_id
         WHERE c.tenant_id = %s
         ORDER BY c.created_at DESC
         LIMIT %s""", (tenant_id, LIMIT))

    out = []
    for r in rows:
        threads, sent = int(r["threads"]), int(r["sent"])
        out.append({
            "id": str(r["id"]), "name": r["name"], "artist": r["artist"],
            "channel": r["channel"], "state": r["state"],
            "bar": _bar(_pct(int(r["agreed"]), threads or 1)),
            "sent": str(sent), "replied": str(r["replied"]),
            "agreed": str(r["agreed"]), "delivered": str(r["delivered"]),
            "insp": (
                Section("Campaign", "kv", (
                    ("name", r["name"]), ("artist", r["artist"]),
                    ("track", r["track"] or "— artist-level, no single recording"),
                    ("channel", r["channel"]), ("goal", r["goal"] or "not stated"),
                    ("state", r["state"]),
                    ("started", _since(r["started_at"]) + " ago" if r["started_at"]
                     else "not started — it opens no threads until it runs"),
                    ("created", _since(r["created_at"]) + " ago"),
                )),
                Section("Funnel", "kv", (
                    ("threads", str(threads)),
                    ("open", str(r["open_threads"])),
                    ("waiting on you", str(r["awaiting"])),
                    ("queued to send", str(r["queued"])),
                    ("sent", str(sent)),
                    ("replies in", str(r["replied"])),
                    ("agreed", str(r["agreed"])),
                    ("delivered", str(r["delivered"])),
                )),
                Section("Note", "note", (
                    "Sent counts messages the provider actually accepted, and nothing has a "
                    "provider yet — so a campaign can be running, have drafts approved and "
                    "queued, and still read zero sent. That gap is the send gate doing its "
                    "job, not a stalled campaign.",
                )) if r["queued"] and not sent else Section("", "note", ()),
                Section("", "actions", (
                    ("Threads", "/threads", ""),
                    (("Pause", f"/campaigns/{r['id']}/state/paused", "", "post")
                     if r["state"] == "running"
                     else ("Run", f"/campaigns/{r['id']}/state/running", "p", "post")),
                    ("Close", f"/campaigns/{r['id']}/state/done", "d", "post"),
                )),
            ),
        })

    running = sum(1 for r in rows if r["state"] == "running")
    return View(
        key="campaigns", title="Campaigns",
        blurb="One track, one channel, one goal. Channels run in parallel because a "
              "channel is a column rather than a code path. Live from campaign.",
        stats=(("campaigns", str(len(out)), ""),
               ("running", str(running), _bar(_pct(running, len(out) or 1))),
               ("threads", str(sum(int(r["threads"]) for r in rows)), ""),
               ("agreed", str(sum(int(r["agreed"]) for r in rows)), ""),
               ("waiting on you", str(sum(int(r["awaiting"]) for r in rows)), "")),
        cols=(Col("name", "Campaign", "b", "24%"), Col("artist", "Artist", "", "16%"),
              Col("channel", "Channel", "chip", "10%"),
              Col("state", "State", "chip", "12%"), Col("bar", "", "bar", "12%"),
              Col("sent", "Sent", "num", "7%"), Col("replied", "Repl", "num", "7%"),
              Col("agreed", "Agr", "num", "6%"), Col("delivered", "Deliv", "num", "6%")),
        rows=tuple(out),
        empty="No campaigns yet. A campaign is one artist, one channel and one goal — "
              "start one from an artist, then it can open threads.",
    )


# -------------------------------------------------------------------- threads

def threads(conn: psycopg.Connection, tenant_id: str) -> View:
    """Every conversation and where it stands.

    The `open` stat counts exactly the rows inside `one_open_thread_per_counterparty`,
    which is what makes it more than a number: it is how many counterparties are
    currently locked, and therefore how much of the shortlist is unavailable to any
    other campaign.
    """
    rows = _rows(conn, """
        SELECT t.id, t.state, t.reason, t.created_at, t.updated_at, t.closed_at,
               t.owner_agent, t.lease_expires_at, t.attempts, t.last_error,
               cp.name AS who, cp.contact_state,
               c.name AS campaign, c.channel,
               p.name AS artist, r.title AS track,
               (SELECT count(*) FROM message m
                 WHERE m.tenant_id = t.tenant_id AND m.thread_id = t.id) AS messages,
               (SELECT max(m.created_at) FROM message m
                 WHERE m.tenant_id = t.tenant_id AND m.thread_id = t.id) AS last_message,
               (SELECT count(*) FROM outbox o
                 WHERE o.tenant_id = t.tenant_id AND o.thread_id = t.id
                 AND o.state = 'pending') AS queued
          FROM thread t
          JOIN party cp ON cp.tenant_id = t.tenant_id AND cp.id = t.counterparty_id
          JOIN campaign c ON c.tenant_id = t.tenant_id AND c.id = t.campaign_id
          JOIN party p ON p.tenant_id = c.tenant_id AND p.id = c.party_id
          LEFT JOIN recording r ON r.tenant_id = c.tenant_id AND r.id = c.recording_id
         WHERE t.tenant_id = %s
         ORDER BY (t.state = 'awaiting_human') DESC, t.updated_at DESC
         LIMIT %s""", (tenant_id, LIMIT))

    # Where each state sits on the fourteen-step walk, so the bar reads as progress
    # rather than as a second copy of the state chip.
    order = ["discovered", "shortlisted", "approved", "drafted", "awaiting_human",
             "queued", "sent", "awaiting_reply", "replied", "negotiating", "agreed",
             "delivered", "verified"]

    out = []
    for r in rows:
        state = r["state"]
        progress = 100 if state == "closed_won" else (
            0 if state in ("closed_lost", "closed_no_reply")
            else _pct(order.index(state) + 1 if state in order else 0, len(order)))
        out.append({
            "id": str(r["id"]), "who": r["who"], "channel": r["channel"],
            "artist": r["artist"], "track": r["track"] or "—",
            "state": state, "bar": _bar(progress),
            "age": _since(r["created_at"]), "last": _since(r["updated_at"]),
            "insp": (
                Section("Thread", "kv", (
                    ("counterparty", r["who"]), ("campaign", r["campaign"]),
                    ("artist", r["artist"]), ("track", r["track"] or "—"),
                    ("channel", r["channel"]), ("state", state),
                    ("why", r["reason"] or "no reason recorded"),
                    ("opened", _since(r["created_at"]) + " ago"),
                    ("last moved", _since(r["updated_at"]) + " ago"),
                    ("closed", _since(r["closed_at"]) + " ago" if r["closed_at"] else "—"),
                )),
                Section("Lock", "kv", (
                    ("holds the counterparty", "yes" if state not in
                     ("closed_won", "closed_lost", "closed_no_reply") else "no — released"),
                    ("contact_state", r["contact_state"]),
                    ("leased by", r["owner_agent"] or "nobody"),
                    ("lease expires", _since(r["lease_expires_at"]) if r["lease_expires_at"]
                     else "—"),
                    ("attempts", str(r["attempts"])),
                    ("last error", r["last_error"] or "none"),
                )),
                Section("Traffic", "kv", (
                    ("messages", str(r["messages"])),
                    ("last message", _since(r["last_message"]) + " ago"
                     if r["last_message"] else "none yet"),
                    ("queued to send", str(r["queued"])),
                )),
                Section("Note", "note", (
                    "While this thread is open the database refuses any other campaign a "
                    "thread with the same counterparty — that is the partial unique index in "
                    "§3c, not a check in application code. Closing it releases them.",
                )),
                # Closing is offered on every open thread and on no closed one, because
                # `advance` would refuse the second and a button that only ever errors is
                # worse than an absent one.
                Section("", "actions", (
                    (("Close won", f"/threads/{r['id']}/close/closed_won", "", "post"),
                     ("Close lost", f"/threads/{r['id']}/close/closed_lost", "d", "post"),
                     ("No reply", f"/threads/{r['id']}/close/closed_no_reply", "d", "post"))
                    if state not in ("closed_won", "closed_lost", "closed_no_reply")
                    else (("Campaigns", "/campaigns", ""),)
                )),
            ),
        })

    open_n = sum(1 for r in rows if r["state"] not in
                 ("closed_won", "closed_lost", "closed_no_reply"))
    return View(
        key="threads", title="Threads",
        blurb="Every conversation, and where it stands. One open thread per counterparty "
              "across the whole label — enforced by the database, not by convention.",
        stats=(("threads", str(len(out)), ""),
               ("open", str(open_n), _bar(_pct(open_n, len(out) or 1))),
               ("awaiting you", str(sum(1 for r in rows if r["state"] == "awaiting_human")), ""),
               ("agreed", str(sum(1 for r in rows if r["state"] in
                                  ("agreed", "delivered", "verified", "closed_won"))), ""),
               ("closed", str(len(out) - open_n), "")),
        cols=(Col("who", "Counterparty", "b", "20%"),
              Col("channel", "Channel", "chip", "9%"), Col("artist", "Artist", "", "14%"),
              Col("track", "Track", "", "12%"), Col("state", "State", "chip", "15%"),
              Col("bar", "", "bar", "10%"), Col("age", "Age", "mono", "6%"),
              Col("last", "Last", "mono", "8%")),
        rows=tuple(out),
        empty="No threads. A campaign opens one per counterparty it decides to approach, "
              "and the database allows exactly one open thread per counterparty.",
    )


# ------------------------------------------------------------------ approvals

def approvals(conn: psycopg.Connection, tenant_id: str) -> tuple[list[dict[str, Any]],
                                                                 tuple[tuple[str, str, str], ...]]:
    """The send gate: every draft sitting on `awaiting_human`, and what it drew on.

    Returns cards rather than a `View` because this screen is a reading surface, not a
    table — the whole argument for it is that you cannot judge a pitch from a row, and a
    row is what a `View` renders.

    The draft selected is the newest unsent outbound message on the thread. A rejected
    draft stays in `message` and a redraft is a second row, so "newest" is what
    distinguishes the current pitch from the history of what was refused.
    """
    rows = _rows(conn, """
        SELECT t.id AS thread_id, t.state, t.updated_at,
               m.id AS message_id, m.subject, m.body, m.created_at, m.channel,
               m.idempotency_key,
               cp.name AS who, c.name AS campaign, c.channel AS campaign_channel,
               p.name AS artist, r.title AS track,
               (SELECT count(*) FROM message x
                 WHERE x.tenant_id = t.tenant_id AND x.thread_id = t.id
                 AND x.direction = 'outbound') AS drafts
          FROM thread t
          JOIN party cp ON cp.tenant_id = t.tenant_id AND cp.id = t.counterparty_id
          JOIN campaign c ON c.tenant_id = t.tenant_id AND c.id = t.campaign_id
          JOIN party p ON p.tenant_id = c.tenant_id AND p.id = c.party_id
          LEFT JOIN recording r ON r.tenant_id = c.tenant_id AND r.id = c.recording_id
          JOIN LATERAL (
                SELECT id, subject, body, created_at, channel, idempotency_key
                  FROM message
                 WHERE tenant_id = t.tenant_id AND thread_id = t.id
                   AND direction = 'outbound' AND sent_at IS NULL
                 ORDER BY created_at DESC
                 LIMIT 1) m ON true
         WHERE t.tenant_id = %s AND t.state = 'awaiting_human'
         ORDER BY m.created_at
         LIMIT %s""", (tenant_id, LIMIT))

    out = []
    for r in rows:
        # What the draft could have stood on. Shown as what exists rather than as what
        # the drafter actually read, because nothing records the latter yet and claiming
        # otherwise would be the drift failure this product is most exposed to.
        # `status = 'live'` matters: a superseded fact is one the system has already
        # replaced, and showing it as something a pitch stands on would be quoting a
        # retracted claim back at the operator approving the send.
        basis = _rows(conn, """
            SELECT dimension, value_text, provenance, confidence
              FROM party_fact
             WHERE tenant_id = %s AND status = 'live' AND party_id = (
                   SELECT c.party_id FROM campaign c
                     JOIN thread t ON t.tenant_id = c.tenant_id
                                  AND t.campaign_id = c.id
                    WHERE c.tenant_id = %s AND t.id = %s)
             ORDER BY confidence DESC NULLS LAST
             LIMIT 6""", (tenant_id, tenant_id, r["thread_id"]))

        out.append({
            "id": str(r["message_id"]), "thread_id": str(r["thread_id"]),
            "who": r["who"], "channel": r["campaign_channel"],
            "artist": r["artist"], "track": r["track"] or "—",
            "waiting": _since(r["created_at"]),
            "subject": r["subject"] or "(no subject)",
            "body": r["body"],
            "insp": (
                Section("Draft", "kv", (
                    ("counterparty", r["who"]), ("campaign", r["campaign"]),
                    ("artist", r["artist"]), ("track", r["track"] or "—"),
                    ("channel", r["channel"]), ("state", r["state"]),
                    ("written", _since(r["created_at"]) + " ago"),
                    ("draft", f"{r['drafts']} on this thread"),
                    ("idempotency key", r["idempotency_key"][:28] + "…"),
                )),
                Section("What it could stand on", "chain", tuple(
                    (0, "fact",
                     f"{b['dimension']} = {b['value_text']} · {b['provenance']}",
                     "party_fact" + (f" · {b['confidence']:.2f}"
                                     if b["confidence"] is not None else " · no confidence"))
                    for b in basis) or ((0, "fact", "nothing recorded for this artist",
                                         "party_fact"),)),
                Section("Checks", "kv", (
                    ("open thread elsewhere", "none — the index guarantees it"),
                    ("approved by", "nobody yet"),
                    ("what approving does", "writes the outbox row, moves the thread to "
                                            "queued, and sends nothing"),
                )),
                Section("Note", "note", (
                    "Approving prepares the send in one transaction — the message is stamped, "
                    "the outbox row is written, the thread moves. Nothing claims the outbox, "
                    "so no mail leaves. That is the honest state of this build and it is "
                    "visible here rather than behind a button that pretends.",
                )),
                Section("", "actions", (
                    ("Approve", f"/approvals/{r['message_id']}/approve", "p", "post"),
                    ("Reject", f"/approvals/{r['message_id']}/reject", "d", "post"),
                    ("Thread", f"/threads?sel={r['thread_id']}", ""),
                )),
            ),
        })

    oldest = _since(rows[0]["created_at"]) if rows else "—"
    queued = _one(conn, "SELECT count(*) AS n FROM outbox WHERE tenant_id = %s "
                        "AND state = 'pending'", (tenant_id,)).get("n", 0)
    stats = (("waiting", str(len(out)), ""),
             ("oldest", oldest, ""),
             ("queued, unsent", str(queued), ""),
             ("sender", "not wired", ""),
             ("sends today", "0", ""))
    return out, stats


# ---------------------------------------------------------------------- inbox

#: How a classified intent reads on the card, and what it implies. Kept as a table
#: because the classifier writes the left column and a person reads the right one, and
#: the mapping is a product decision rather than a model output.
_INTENT_LABEL = {
    "interested":    ("interested", "needs you"),
    "declined":      ("declined", "handled"),
    "question":      ("asked a question", "needs you"),
    "out_of_office": ("out of office", "handled"),
    "unsubscribe":   ("asked to stop", "needs you"),
    "bounce":        ("bounced", "needs you"),
    "unclear":       ("unclear", "needs you"),
    "":              ("unclassified", "needs you"),
}


def inbox(conn: psycopg.Connection, tenant_id: str) -> tuple[list[dict[str, Any]],
                                                             tuple[tuple[str, str, str], ...]]:
    """Replies, newest first.

    **Nothing writes inbound messages yet.** There is no mail provider and no inbound
    adapter, so this reads a real table that is legitimately empty, and the empty state
    says so. That is a better answer than a fixture: an operator who sees three invented
    replies has no way to learn that the integration is missing.

    `outreach.record_reply` is the writer, and it works — the tests drive it. What is
    absent is the thing that would call it.
    """
    rows = _rows(conn, """
        SELECT m.id, m.subject, m.body, m.intent, m.confidence, m.received_at,
               m.channel, m.thread_id,
               t.state AS thread_state,
               cp.name AS who, p.name AS artist, c.name AS campaign
          FROM message m
          JOIN thread t ON t.tenant_id = m.tenant_id AND t.id = m.thread_id
          JOIN party cp ON cp.tenant_id = t.tenant_id AND cp.id = t.counterparty_id
          JOIN campaign c ON c.tenant_id = t.tenant_id AND c.id = t.campaign_id
          JOIN party p ON p.tenant_id = c.tenant_id AND p.id = c.party_id
         WHERE m.tenant_id = %s AND m.direction = 'inbound'
         ORDER BY m.received_at DESC
         LIMIT %s""", (tenant_id, LIMIT))

    out = []
    for r in rows:
        label, state = _INTENT_LABEL.get(r["intent"], ("unclassified", "needs you"))
        body = (r["body"] or "").strip()
        out.append({
            "id": str(r["id"]), "thread_id": str(r["thread_id"]),
            "who": r["who"], "artist": r["artist"], "intent": label, "state": state,
            "when": _since(r["received_at"]) + " ago",
            "preview": body[:180] + ("…" if len(body) > 180 else ""),
            "insp": (
                Section("Message", "kv", (
                    ("from", r["who"]), ("artist", r["artist"]),
                    ("campaign", r["campaign"]), ("channel", r["channel"]),
                    ("received", _since(r["received_at"]) + " ago"),
                    ("classified", label),
                    ("confidence", f"{r['confidence']:.2f}" if r["confidence"]
                     else "— not classified"),
                    ("thread state", r["thread_state"]),
                )),
                Section("Full reply", "quote", (body or "(empty)",)),
                Section("", "actions", (
                    ("Open thread", f"/threads?sel={r['thread_id']}", ""),)),
            ),
        })

    needs = sum(1 for r in out if r["state"] == "needs you")
    stats = (("replies", str(len(out)), ""),
             ("needs you", str(needs), _bar(_pct(needs, len(out) or 1))),
             ("classified", str(sum(1 for r in rows if r["intent"])), ""),
             ("inbound adapter", "not wired", ""),
             ("threads awaiting reply",
              str(_one(conn, "SELECT count(*) AS n FROM thread WHERE tenant_id = %s "
                             "AND state = 'awaiting_reply'", (tenant_id,)).get("n", 0)), ""))
    return out, stats


# ---------------------------------------------- creating outreach by hand
#
# A live view of a table nobody can fill is not a built screen — it is a wireframe with
# a real query behind it. These are the surfaces that let an operator produce the rows,
# so the whole loop (campaign → threads → draft → gate → queued) runs without SQL.
#
# The drafter, the sender and the inbox agents would each remove one of these. Until
# they exist the operator is the fleet, which is the correct order: the screen that
# governs an agent should work before the agent does, or there is nothing to govern it
# with on the day it turns on.

#: `campaign.campaign_channel_known`. Duplicated from the CHECK on purpose — the closed
#: set has to be readable without a connection, and the constraint is what enforces it.
CHANNELS: tuple[tuple[str, str], ...] = (
    ("curator", "Curator — playlists and programmers"),
    ("ugc", "UGC — creators and short video"),
    ("press", "Press — writers and outlets"),
    ("radio", "Radio — stations and shows"),
    ("sync", "Sync — supervisors and libraries"),
)


def new_campaign_sections(conn: psycopg.Connection, tenant_id: str, *,
                          form: dict[str, str] | None = None,
                          error: str = "") -> tuple[Section, ...]:
    """The inspector at `?sel=new`, offering only what exists.

    The artist select is the live roster and the recording select is the live catalogue,
    so a campaign cannot be created against something that is not there. That is worth
    more than a free-text field here: the whole value of a campaign row is the join it
    carries, and a typo'd name would produce a campaign that joins to nothing and
    renders as blank columns.
    """
    form = form or {}
    artists = _rows(conn, """
        SELECT id, name FROM party
         WHERE tenant_id = %s AND party_class = 'roster' AND status = 'active'
         ORDER BY name""", (tenant_id,))
    recordings = _rows(conn, """
        SELECT id, title FROM recording WHERE tenant_id = %s ORDER BY title""",
        (tenant_id,))

    if not artists:
        return (
            Section("No artists", "note", (
                "A campaign belongs to an artist and the roster is empty. Add one from "
                "Artists first — the campaign has nowhere to hang otherwise.",)),
            Section("", "actions", (("Artists", "/artists", "p"),)),
        )

    return (
        Section("New campaign", "form", (
            Field("name", "Name", "text", form.get("name", ""), required=True,
                  placeholder="Cold Open — curators",
                  hint="What you will recognise it by in a list of thirty."),
            Field("party_id", "Artist", "select", form.get("party_id", ""),
                  _flat(tuple((str(a["id"]), a["name"]) for a in artists)),
                  hint="Lessons accumulate on the artist and are inherited by every "
                       "release, which is why the campaign hangs here."),
            Field("recording_id", "Recording", "select", form.get("recording_id", ""),
                  _flat((("", "— none: an artist-level push"),)
                        + tuple((str(r["id"]), r["title"]) for r in recordings)),
                  hint="Optional. A profile feature or a residency announcement is a "
                       "real campaign with no single recording behind it."),
            Field("channel", "Channel", "select", form.get("channel", "curator"),
                  _flat(CHANNELS),
                  hint="Channels run in parallel because a channel is a column rather "
                       "than a code path."),
            Field("goal", "Goal", "text", form.get("goal", ""),
                  placeholder="playlist adds on editorial-adjacent lists",
                  hint="Free text. Read by a person, not parsed."),
        ), action="/campaigns", submit="Create campaign", error=error),
        Section("", "actions", (("Cancel", "/campaigns", ""),)),
        Section("What happens next", "note", (
            "It is created in `draft` and opens no threads. Running it is a second, "
            "deliberate act — the same shape as the send gate, one step earlier.",
        )),
    )


def shortlist_candidates(conn: psycopg.Connection, tenant_id: str,
                         limit: int = 25) -> list[dict[str, Any]]:
    """Counterparties this campaign could open a thread with.

    `contact_state = 'contactable'` is the whole filter, and it is the denormalised
    mirror of the partial unique index — so this returns exactly the people no other
    campaign is currently talking to. Migration 009 argued that column into existence
    for the accelerated vector search; it earns its keep again here, in plain SQL, for
    the same reason.

    Ordered by whether they can be searched at all, because a counterparty with no
    embedding is invisible to R1 and is therefore the one a human most needs to see.
    """
    return _rows(conn, """
        SELECT p.id, p.name, p.contact_state,
               (p.profile_embedding IS NOT NULL) AS searchable,
               (SELECT string_agg(r.role, ', ') FROM party_role r
                 WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id) AS roles
          FROM party p
         WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
           AND p.contact_state = 'contactable'
         ORDER BY (p.profile_embedding IS NOT NULL) DESC, p.name
         LIMIT %s""", (tenant_id, limit))


def draft_form_section(thread_id: str, *, form: dict[str, str] | None = None,
                       error: str = "") -> Section:
    """Write the pitch by hand, in the inspector, on the thread it belongs to.

    This is the Drafter's job done by a person. It writes the same `message` row the
    agent would, moves the thread through the same states, and lands on the same gate —
    so the approvals screen is exercised by real drafts rather than by a fixture, and
    the day the Drafter is written it replaces this and nothing downstream changes.
    """
    form = form or {}
    return Section("Write the pitch", "form", (
        Field("subject", "Subject", "text", form.get("subject", ""), required=True,
              placeholder="Cold Open — thought it might suit the list"),
        Field("body", "Body", "textarea", form.get("body", ""), required=True,
              placeholder="Short, specific, and easy to say no to.",
              hint="Goes to the approvals screen, not to anybody. Approving queues it; "
                   "nothing sends it."),
    ), action=f"/threads/{thread_id}/draft", submit="Save draft", error=error)


def justification_sections(conn: psycopg.Connection, tenant_id: str,
                           thread_id: str) -> tuple[Section, ...]:
    """Why this counterparty, answered from the index as it was — or why we cannot say.

    The inspector half of `outreach.justification`. This is the beat the whole submission
    is built around, so what it renders when it *cannot* answer matters as much as what
    it renders when it can, and the three cases are deliberately three different panels
    rather than one panel with a blank field.

      * **A ranking.** The shortlist as it stood at the instant the thread opened, with
        the recorded position beside the replayed one, and a line saying whether they
        agree. Disagreement is shown, not hidden: it means the index has moved under the
        record — a changed embedding model, most likely — and an operator seeing "the
        replay no longer agrees" learns something true, where a silently reordered list
        teaches them something false.
      * **Nobody ranked it.** A human opened this thread. That is legitimate and the
        panel says so plainly, because the alternative — an empty ranking — reads as a
        system that lost the answer rather than one that never had it.
      * **The history has expired.** We recorded a reason and the cluster no longer keeps
        the memory to reconstruct it. The panel says that, and says why, because "we knew
        and can no longer show you" is a different sentence from "we never knew" and an
        operator standing in front of somebody asking why they were emailed needs the
        right one.

    Never falls back to the live shortlist. It would render beautifully and be a
    fabrication — see `agents.shortlist_as_of`.
    """
    # Imported here rather than at module scope, like `settings_mod` and `distributors`
    # above: `outreach` imports `agents`, and `agents` imports enough of this package
    # that a top-level import here is a cycle waiting for the next module to join it.
    from rtf_platform import agents, outreach

    try:
        found = outreach.justification(conn, tenant_id, thread_id)
    except outreach.NotJustified:
        return (Section("Why this counterparty", "note", (
            "Nobody ranked them — this thread was opened by hand, so there is no "
            "shortlist behind it to replay. That is recorded as an absence rather than "
            "filled in with the ranking at the moment somebody typed the name.",)),)
    except agents.HistoryExpired:
        return (Section("Why this counterparty", "note", (
            "A ranking justified this thread, and the cluster no longer retains the "
            "memory to reconstruct it — time travel is bounded by gc.ttlseconds, and the "
            "threshold only moves forward. The decision was recorded; the index it read "
            "has been collected.",)),)

    agreed = ("the replay agrees with what was recorded"
              if found["matched"] else
              "THE REPLAY NO LONGER AGREES — the index has moved under this record")
    header = Section("Why this counterparty", "kv", (
        ("recorded rank", f"#{found['recorded_rank']}"),
        ("replayed rank", f"#{found['replayed_rank']}" if found["replayed_rank"] else "not in the list"),
        ("distance then", f"{found['recorded_distance']:.4f}"),
        ("read at", found["as_of"]),
        ("check", agreed),
    ))
    ranking = Section(
        "The shortlist, as it was", "chain",
        tuple(f"#{i} {row['name']} — {float(row['distance']):.4f}"
              for i, row in enumerate(found["ranking"], start=1)))
    return (header, ranking)


#: The windows the console scrubber offers, in the order a person reads them.
#:
#: Bounded by what the cluster can actually serve rather than by what would be nice:
#: `gc.ttlseconds` is seven days but the GC threshold only moves forward from the moment
#: it was raised, so reachable depth grows rather than jumping. Measured 2026-08-13 —
#: -10h resolved, -18h did not. Offering a window that raises `HistoryExpired` on every
#: press would be a control that exists to disappoint; these are the ones that answer.
#:
#: `agents.shortlist_as_of` validates the value again on the way in. Two independent
#: checks on something interpolated into SQL is the right number, and this list being one
#: of them is not a reason for the other to relax.
SCRUB_WINDOWS: tuple[tuple[str, str], ...] = (
    ("now", ""), ("15 min ago", "-15m"), ("1 hour ago", "-1h"),
    ("3 hours ago", "-3h"), ("6 hours ago", "-6h"),
)


def ranked_shortlist_sections(conn: psycopg.Connection, tenant_id: str, *,
                              campaign_id: str, artist_id: str,
                              as_of: str = "") -> tuple[Section, ...]:
    """R1's ranking for this campaign's artist, live or as it stood.

    The console's other shortlist — `shortlist_candidates` — answers "who may we
    contact", which is a state question the partial unique index settles. This answers
    "who should we contact, and in what order", which is the vector search, and the two
    are deliberately separate sections rather than one merged list: an operator deciding
    who to pitch is doing a different thing from an operator checking who is free.

    The scrubber is the point. Running the same query at `-3h` and reading a different
    order is the product's central claim made visible in one control — lessons
    accumulate, genres land, threads open, and the ranking those produce is a thing that
    existed at a time rather than a fact about the world.

    An expired window renders as a refusal and never as the live list. `shortlist_as_of`
    raises `HistoryExpired` past the GC boundary and this catches it to say so; falling
    back would put today's ranking under a label saying Tuesday, which is the one
    outcome the whole feature exists to prevent.
    """
    from rtf_platform import agents

    picker = Section(
        "The shortlist, as of", "actions",
        tuple((label, f"/campaigns?sel={campaign_id}&as_of={window}", "s", "get")
              for label, window in SCRUB_WINDOWS))

    # An unrecognised window REFUSES. The obvious sanitiser — fall back to the live
    # ranking when the value is not in the list — was what this function shipped with for
    # about ten minutes, and it is the exact silent fallback this codebase forbids
    # everywhere else: `?as_of=-72h` rendered today's shortlist, correctly labelled
    # "Ranked now", to somebody who had asked for three days ago. Nothing was wrong on
    # screen. Nothing was right either, because the question went unanswered and
    # unmentioned.
    #
    # The empty string is the one non-member that means something — it is "now", the
    # default when no window is chosen at all.
    known = {window for _, window in SCRUB_WINDOWS}
    if as_of and as_of not in known:
        return (picker, Section("Not a window", "note", (
            f"{as_of!r} is not one of the windows this console offers. It is not being "
            "quietly rounded to the live ranking: the value would reach an AS OF SYSTEM "
            "TIME clause, and a control that answers a question you did not ask is worse "
            "than one that says no.",)))

    try:
        ranked = agents.shortlist_as_of(conn, tenant_id, artist_id, as_of=as_of)
    except agents.HistoryExpired:
        return (picker, Section("Not reachable", "note", (
            "That window is past the cluster's garbage-collection threshold, so the "
            "index as it stood then no longer exists to be read. The ranking is not "
            "being approximated from today's — it is gone, and saying so is the only "
            "honest answer.",)))
    except ValueError:
        # Unreachable while SCRUB_WINDOWS holds only shapes `shortlist_as_of` accepts,
        # and kept because that is a property of a constant somebody will edit. The two
        # validations are deliberately independent; this is what makes them so.
        return (picker, Section("Not a window", "note", (
            "That value did not survive validation on the way into the query.",)))

    if not ranked:
        return (picker, Section("Nothing to rank", "note", (
            "This artist has no profile embedding, so R1 has nothing to search from. "
            "Embed the artist and the ranking appears.",)))

    when = next((label for label, w in SCRUB_WINDOWS if w == as_of), "now")
    return (picker, Section(
        f"Ranked {when}", "chain",
        tuple(f"#{i} {row['name']} — {float(row['distance']):.4f}"
              for i, row in enumerate(ranked, start=1))))
