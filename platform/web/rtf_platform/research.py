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
          FROM artist_fact f
          LEFT JOIN artist a ON a.id = f.artist_id
         WHERE f.tenant_id = %s
         ORDER BY f.observed_at DESC
         LIMIT %s""", (tenant_id, LIMIT))

    counts = _one(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status = 'live')       AS live,
               count(*) FILTER (WHERE status = 'stale')      AS stale,
               count(*) FILTER (WHERE status = 'retracted')  AS retracted
          FROM artist_fact WHERE tenant_id = %s""", (tenant_id,))

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
                Section("Stands on", "chain", tuple(_basis(conn, "artist_fact", r["id"]))),
                Section("Supports", "chain", tuple(_dependents(conn, "artist_fact", r["id"]))),
                Section("", "actions", ("Relevant", "Not relevant", "Retract", "Recheck")),
            ),
        })

    return View(
        key="facts", title="Facts",
        blurb="Everything the fleet believes, and what each belief stands on. Live from "
              "artist_fact.",
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
          LEFT JOIN artist a ON a.id = l.artist_id
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
                        tuple(_trail(conn, r["id"]))),
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


def _trail(conn: psycopg.Connection, lead_id: Any) -> list[tuple]:
    """The attention trail — a recursive walk up `parent_lead_id`.

    This is the query that answers *why were we even looking here?* for any document,
    fact or contact the system holds. It is the provenance record and, for a
    counterparty, the compliance artifact at the same time.
    """
    rows = _rows(conn, """
        WITH RECURSIVE up(id, parent_lead_id, kind, target, depth, hop) AS (
            SELECT id, parent_lead_id, kind, target, depth, 0 FROM lead WHERE id = %s
            UNION ALL
            SELECT l.id, l.parent_lead_id, l.kind, l.target, l.depth, up.hop + 1
              FROM lead l JOIN up ON l.id = up.parent_lead_id
             WHERE up.hop < 8
        )
        SELECT kind, target, depth, hop FROM up ORDER BY hop""", (lead_id,))
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
          LEFT JOIN artist a ON a.id = r.artist_id
         WHERE r.tenant_id = %s
         ORDER BY r.started_at DESC LIMIT %s""", (tenant_id, LIMIT))

    counts = _one(conn, """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE state = 'error')   AS errors,
               count(*) FILTER (WHERE state = 'refused') AS refused,
               coalesce(sum(cost_micro_usd), 0)          AS micro
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
                 WHERE r.artist_id = a.id AND r.started_at > now() - INTERVAL '1 hour')
                 AS spent,
               (SELECT coalesce(sum(cost_micro_usd), 0) FROM agent_run r
                 WHERE r.artist_id = a.id AND r.started_at > now() - INTERVAL '24 hours')
                 AS micro,
               (SELECT count(*) FROM lead l
                 WHERE l.artist_id = a.id AND l.state = 'pending') AS pending
          FROM artist a
          LEFT JOIN artist_budget b ON b.artist_id = a.id
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
    rows = _rows(conn, """
        SELECT t.id, t.title, t.slug, t.isrc, t.released_on, t.status,
               a.name AS artist_name,
               (SELECT count(*) FROM artist_fact f WHERE f.track_id = t.id) AS facts,
               (SELECT count(*) FROM lead l WHERE l.track_id = t.id)         AS leads
          FROM track t JOIN artist a ON a.id = t.artist_id
         WHERE t.tenant_id = %s ORDER BY a.name, t.title""", (tenant_id,))

    out = [{
        "id": str(r["id"]), "title": r["title"], "artist": r["artist_name"],
        "state": r["status"], "bpm": "—", "key": "—",
        "campaigns": str(r["leads"]), "streams": "—", "spark": "▁▁▁▁▁▁▁",
        "insp": (
            Section("Track", "kv", (
                ("title", r["title"]), ("artist", r["artist_name"]),
                ("isrc", r["isrc"] or "—"),
                ("released", str(r["released_on"]) if r["released_on"] else "—"),
                ("status", r["status"]), ("facts", str(r["facts"])),
                ("leads", str(r["leads"])),
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
        blurb="Analysed once. Every campaign, pitch and asset is a query against these "
              "facts rather than a re-analysis. Live from track.",
        stats=(("tracks", str(len(out)), ""), ("analysed", "0", ""),
               ("with leads", str(sum(1 for r in out if r["campaigns"] != "0")), ""),
               ("facts", str(sum(int(r["insp"][0].items[5][1]) for r in out)), ""),
               ("shown", str(len(out)), "")),
        cols=(Col("title", "Track", "b", "24%"), Col("artist", "Artist", "", "20%"),
              Col("state", "Status", "chip", "12%"), Col("bpm", "BPM", "num", "8%"),
              Col("key", "Key", "mono", "10%"), Col("campaigns", "Leads", "num", "8%"),
              Col("streams", "30d", "num", "9%"), Col("spark", "", "spark", "9%")),
        rows=tuple(out),
        empty="No tracks yet. Add one from an artist — everything downstream hangs off it.",
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
        return (danger, cancel, edit, record, where, add, note)

    #: A link, not a submit. Deleting an artist cascades, so it takes two deliberate
    #: acts and the second one names what goes with it.
    danger = Section("", "actions", (
        ("Delete artist", f"/artists?sel={aid}&confirm=delete", "d"),
    ))
    return (edit, record, where, add, note, danger)


# ------------------------------------------------------------------- artists

def artists(conn: psycopg.Connection, tenant_id: str, *,
            editing_id: str | None = None, error: str = "",
            profile_error: str = "", confirm_delete: bool = False) -> View:
    rows = _rows(conn, """
        SELECT a.id, a.name, a.type, a.slug, a.status, a.created_at,
               (SELECT count(*) FROM track t        WHERE t.artist_id = a.id) AS tracks,
               (SELECT count(*) FROM artist_fact f  WHERE f.artist_id = a.id
                  AND f.status = 'live')                                      AS facts,
               (SELECT count(*) FROM lead l         WHERE l.artist_id = a.id
                  AND l.state = 'pending')                                    AS pending,
               (SELECT count(*) FROM artist_profile p WHERE p.artist_id = a.id
                  AND p.mode <> 'absent')                                     AS profiles,
               (SELECT count(*) FROM artist_document d WHERE d.artist_id = a.id) AS docs
          FROM artist a WHERE a.tenant_id = %s ORDER BY a.name""", (tenant_id,))

    out = []
    for r in rows:
        # `id` is needed because each surface is its own delete target, and the tenant
        # predicate is here rather than implied by the artist — a scoped delete link
        # built from an unscoped read is how one label removes another's row.
        profiles = _rows(conn, """
            SELECT id, platform, mode, handle, profile_url FROM artist_profile
             WHERE tenant_id = %s AND artist_id = %s
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
