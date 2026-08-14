"""Rows to JSON. Pure functions, one per entity, and no database anywhere in the file.

This is the half of `research.py` that the console does in Jinja and the API has to do
itself. It exists as its own module for one practical reason: **every function here is
testable without a cluster.** The suite refuses to run against production (see
`tests/conftest.py`), so anything that needs a connection is skipped in the default run
— and the shaping is where the bugs a client would actually feel live. Fabricate a row
dict, call the function, assert on the result.

## The three rules

**Absent stays absent.** `None` is emitted as `null`, never as `"—"`, `""`, `0` or
`"unknown"`. The console renders an em-dash because a person reads a table; a client
that received one could not tell a missing confidence from a fact confidently scored
"—". This is the no-silent-defaults rule applied to serialisation, and it is the single
biggest difference between this module and `demo.View`.

**Numbers stay numbers.** A score is a float, a count is an int, a percentage is a
number and not `"73%"`. The console pre-formats because it is about to concatenate into
HTML; a client wants to sort, threshold and chart, and cannot do any of those to a
string. `_bar()`'s ten-cell text bar has no equivalent here at all — it is a rendering
of a ratio, and the ratio is what gets sent.

**Money stays in micro-dollars.** `cost_micro_usd` is an integer in the schema
precisely so that summing spend never drifts, and dividing it by a million on the way
out to produce a float would throw that away in the last hop. The client divides, or
better, formats from the integer. The field keeps its `_micro_usd` suffix so nobody
mistakes it for dollars.

## Timestamps

ISO 8601 with an offset, from `datetime.isoformat()`. Not epoch seconds — unreadable in
a log and ambiguous about precision — and not the console's `"%m-%d %H:%M"`, which has
no year and no zone and is a rendering for somebody who already knows both. A `date`
(as opposed to a `datetime`) serialises as `YYYY-MM-DD`, because `released_on` genuinely
is a day and giving it a midnight would invent a precision the row does not have.

## What is deliberately absent

**No envelope-level `_links`, no HATEOAS.** The client is a React console written
against this document, not a crawler.

**No field-name translation layer.** A column called `contact_state` is sent as
`contact_state`. Renaming to camelCase would put a second vocabulary between the schema
and the client, and every future column would need a decision nobody would remember.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from rtf_platform import research


def scalar(value: Any) -> Any:
    """One database value as JSON.

    `Decimal` becomes `float`. That is a lossy conversion and it is the right one here:
    the two `Decimal` columns that reach this function are `score` and `confidence`,
    both bounded 0–1 ratios where a float carries far more precision than the number
    means. Money never reaches it — `cost_micro_usd` is an `int` and stays one, which is
    the whole reason the schema stores micro-dollars.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [scalar(v) for v in value]
    if isinstance(value, dict):
        return {k: scalar(v) for k, v in value.items()}
    # Anything else is a type this function has not been taught. `str()` would be the
    # silent default: it always produces something, and the something would look
    # plausible in the response and be wrong in a way nobody could see. Raise instead.
    raise TypeError(f"no JSON shape for {type(value).__name__}: {value!r}")


def pick(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The named columns, JSON-shaped, and a `KeyError` for one that is not there.

    Not `row.get(key)`. A misspelled column would then serialise as `null`, which is a
    legal value for most of these fields — so the response would be well-formed, the
    client would render "unknown", and nothing anywhere would say the server had been
    asked for a column that does not exist.
    """
    return {k: scalar(row[k]) for k in keys}


# ------------------------------------------------------------------ envelopes

def listing(rows: list[dict[str, Any]], shape, *,
            total: int | None = None, **extra: Any) -> dict[str, Any]:
    """A list response.

    `truncated` is the field worth explaining. Every list query in `research.py` carries
    `LIMIT 200`, and a response that just stopped at 200 with no marker would be a
    silent default of the worst kind — the client believes it has the table, and it has
    a page. So the limit is stated, and so is whether it was reached.

    When a total is known (the views that already compute one for their header) it is
    sent and `truncated` is exact. When it is not, `truncated` is `returned >= limit`,
    which is an **upper bound**: a table holding exactly 200 rows reports `true` while
    having been sent whole. That imprecision is deliberate — the alternative is a second
    `count(*)` per list endpoint against a cluster billed by round trip, to distinguish
    a case in which the client has all the data either way.
    """
    out = [shape(r) for r in rows]
    known = total is not None
    return {
        "rows": out,
        "limit": research.LIMIT,
        "returned": len(out),
        "total": total,
        "truncated": (total > len(out)) if known else (len(out) >= research.LIMIT),
        "truncated_is_exact": known,
        **extra,
    }


# --------------------------------------------------------------------- facts

def fact(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "dimension", "value_text", "provenance", "status",
                "confidence", "source", "written_by", "observed_at", "model",
                "supersedes_id", "artist_name")


# --------------------------------------------------------------------- queue

def lead(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "kind", "adapter", "target", "depth", "score", "state",
                "owner_agent", "lease_expires_at", "next_action_at", "attempts",
                "last_error", "cadence_seconds", "scope_kind", "reason",
                "parent_lead_id", "artist_name")


# ---------------------------------------------------------------------- runs

def run(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, "id", "agent_kind", "state", "summary", "error", "documents",
               "facts", "metrics", "leads", "dropped", "tokens_in", "tokens_out",
               "cost_micro_usd", "duration_ms", "started_at", "artist_name")
    # `refused_json` is whatever the spend gate wrote. Passed through as the object it
    # is rather than stringified, because a client that wants to show *why* a run was
    # refused needs the fields, and `str(dict)` is not JSON.
    out["refused"] = scalar(r["refused_json"])
    return out


# ------------------------------------------------------------------- budgets

def budget(r: dict[str, Any]) -> dict[str, Any]:
    """Tokens spent against a cap, with the ratio left to the client.

    `cap` is `coalesce`d to 20000 in the query, so it is never null here; a cap of 0
    would still be a legal row and dividing by it is the client's problem to notice,
    which is why the ratio is not precomputed.
    """
    out = pick(r, "id", "name", "cap", "paused", "max_depth", "max_leads",
               "spent", "pending")
    out["cost_micro_usd_24h"] = int(r["micro"])
    return out


# ------------------------------------------------------------- counterparties

def counterparty(r: dict[str, Any]) -> dict[str, Any]:
    """`searchable` is a real boolean, and it is the field this endpoint exists for.

    A counterparty with no embedding is invisible to the shortlist no matter how good a
    match they would be. The console renders `"yes"`/`"no"`; a client needs to be able
    to filter and count, so it gets `true`/`false`.

    The profile body is sent whole rather than truncated to the console's 600
    characters. Truncation is a layout decision, and a client that wants a preview can
    take a prefix — while one that received a prefix cannot recover the rest.
    """
    out = pick(r, "id", "name", "contact_state", "embedding_model", "searchable",
               "platform", "url", "roles")
    out["role_list"] = [x.strip() for x in (r["role_list"] or "").split(",") if x.strip()]
    out["profile"] = (r["profile"] or "").strip() or None
    return out


# ------------------------------------------------------------------- artists

def artist(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "name", "type", "slug", "kind", "status", "created_at",
                "tracks", "facts", "pending", "profiles", "docs")


def presence(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "platform", "mode", "handle", "profile_url", "state",
                "match_basis")


# -------------------------------------------------------------------- tracks

def recording(r: dict[str, Any], assets: list[dict[str, Any]],
              credits: list[dict[str, Any]]) -> dict[str, Any]:
    """A recording with its masters and credits attached.

    `artist_name` is `coalesce`d to an em-dash *in the SQL* — a rendering decision that
    predates this module and is shared with the console, so undoing it here is the one
    place where a `"—"` is translated back to `null`. Doing that translation is worth
    the ugliness: a client should not have to know that one column carries the console's
    typography.
    """
    out = pick(r, "id", "title", "slug", "isrc", "isrc_raw", "released_on", "status",
               "created_at", "facts", "leads", "places")
    name = r["artist_name"]
    out["artist_name"] = None if name in (None, "—") else name
    out["assets"] = [asset(a) for a in assets]
    out["credits"] = [credit(c) for c in credits]
    return out


def asset(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "kind", "label", "bytes", "mime", "state", "uploaded_at",
                "uploaded_by", "duration_ms", "sample_rate")


def credit(r: dict[str, Any]) -> dict[str, Any]:
    return pick(r, "id", "party_id", "party_name", "role", "provenance")


# ----------------------------------------------------------------- campaigns

def campaign(r: dict[str, Any]) -> dict[str, Any]:
    """The campaign, with its funnel as an object rather than eight loose keys.

    Grouped because they are one thing — every count is derived from `thread` and
    `message` at read time, none is stored — and because a client charting the funnel
    wants the stages together and in order.
    """
    out = pick(r, "id", "name", "channel", "state", "goal", "started_at",
               "created_at", "artist", "track")
    out["funnel"] = pick(r, "threads", "open_threads", "awaiting", "queued", "sent",
                         "replied", "agreed", "delivered")
    return out


# ------------------------------------------------------------------- threads

def thread(r: dict[str, Any]) -> dict[str, Any]:
    """A thread, plus the two derived facts a client should not re-derive.

    `holds_counterparty` is not cosmetic: it is whether this row is inside
    `one_open_thread_per_counterparty` and therefore whether this counterparty is
    unavailable to every other campaign. `outreach.CLOSED` is the single definition of
    that, imported rather than restated, so the API cannot come to disagree with the
    partial index about what "open" means.

    `progress` comes from `research.thread_progress` for the same reason.
    """
    from rtf_platform import outreach

    out = pick(r, "id", "state", "reason", "created_at", "updated_at", "closed_at",
               "owner_agent", "lease_expires_at", "attempts", "last_error",
               "who", "contact_state", "campaign", "channel", "artist", "track",
               "messages", "last_message", "queued")
    out["holds_counterparty"] = r["state"] not in outreach.CLOSED
    out["progress"] = research.thread_progress(r["state"])
    return out


# ----------------------------------------------------------------- approvals

def draft(r: dict[str, Any], basis: list[dict[str, Any]]) -> dict[str, Any]:
    """A draft waiting at the send gate, with what it could have stood on.

    `basis` is named `could_stand_on` on the wire, and the clumsiness is on purpose.
    Nothing records what the drafter actually read; these are the live facts that exist
    for this artist. Calling the field `basis` or `evidence` would let a client render
    "this pitch is based on…", which would be a claim the server cannot support — the
    drift failure this product is most exposed to. The name is the disclaimer.
    """
    out = pick(r, "thread_id", "state", "updated_at", "subject", "body",
               "created_at", "channel", "idempotency_key", "who", "campaign",
               "campaign_channel", "artist", "track", "drafts")
    out["id"] = scalar(r["message_id"])
    out["could_stand_on"] = [
        pick(b, "dimension", "value_text", "provenance", "confidence") for b in basis]
    return out


# --------------------------------------------------------------------- inbox

def reply(r: dict[str, Any]) -> dict[str, Any]:
    """An inbound message.

    `intent` is sent raw — `""` when nothing classified it, not the console's
    `"unclassified"` label. The empty string is what the column holds and what the
    absence of a classifier looks like, and mapping it to a word here would make an
    unclassified reply indistinguishable from one a model classified as unclassifiable.
    The console's label table is presentation and stays in `research.py`.
    """
    return pick(r, "id", "thread_id", "subject", "body", "intent", "confidence",
                "received_at", "channel", "thread_state", "who", "artist", "campaign")


# --------------------------------------------------------------------- fleet

def agent(a: dict[str, Any]) -> dict[str, Any]:
    """One agent: what the manifest declares, what the code can run, what has happened.

    `manifest` is `null` for an `unmanifested` worker — a name with `agent_run` rows and
    no row in `agent_manifest`. That null is the answer, not a missing value, and it is
    the reason this endpoint is worth having: it is how you find a worker nobody
    declared.
    """
    m, run_ = a["manifest"], a["runs"]
    return {
        "kind": a["kind"],
        "state": a["state"],
        "has_implementation": a["has_code"],
        "leases_held": a["leases_held"],
        "work_waiting": a["work_waiting"],
        "manifest": None if m is None else pick(
            m, "work_table", "claim_state", "scope_kinds", "adapters", "writes",
            "batch_size", "per_artist_cap", "lease_seconds", "max_attempts",
            "requires_human", "enabled", "updated_at"),
        "runs": {
            "total": int(run_.get("total") or 0),
            "last_hour": int(run_.get("last_hour") or 0),
            "failed": int(run_.get("errors") or 0),
            "refused": int(run_.get("refused") or 0),
            "last_run": scalar(run_.get("last_run")),
            "cost_micro_usd": int(run_.get("cost_micro") or 0),
        },
    }


# --------------------------------------------------------------- suggestions

def suggestion(r: dict[str, Any]) -> dict[str, Any]:
    """One candidate, with the server's verdict on whether accepting it would work.

    `refused_because` is `repo.why_unacceptable` — the same predicate
    `repo.accept_suggestion` branches on, not a second copy of it — so a client can
    disable the Accept control and show the reason on it, rather than making an operator
    discover the refusal by pressing. `null` means the accept path is clear.

    It is computed here rather than in `research.today_items` because it is pure and the
    console does not use it; charging every Jinja render for an answer nothing reads
    would be a cost with no reader.
    """
    from rtf_platform import repo

    out = pick(r, "id", "party_id", "party_name", "party_slug", "kind", "payload",
               "confidence", "rationale")
    out["refused_because"] = repo.why_unacceptable(r["payload"])
    return out


# --------------------------------------------------------------------- today

def _why_suggestion_group(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The reasoning behind a suggestion group, from what the rows actually record.

    Nothing here is composed out of nothing. The count, the platforms and the best
    confidence are on the rows; `provenance` is `"inferred"` because that is what a
    suggestion *is* — an agent matched a name rather than measuring anything, which is
    the entire reason a person is being asked. The per-candidate `rationale` is the
    adapter's own words and is included only where the adapter wrote some.
    """
    group = item["suggestions"]
    steps = [
        {"label": "how it was found",
         "value": "an agent searched a source by name and found these",
         "provenance": "inferred"},
        {"label": "candidates", "value": str(len(group))},
        {"label": "platforms", "value": ", ".join(item["platforms"]) or "search"},
        {"label": "best match", "value": f"{item['best_confidence']:.2f}"},
    ]
    for row in group:
        if row.get("rationale"):
            steps.append({"label": "why this candidate", "value": row["rationale"],
                          "provenance": "inferred"})
    return steps


def _why_parked_lead(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Why a lead is parked, from the lead row.

    No `provenance` on any step. The vocabulary in this codebase — `measured`,
    `inferred`, `asserted` — belongs to `party_fact` and means something specific about
    how a *claim* was arrived at. An attempt count is not a claim, and borrowing the
    word to decorate it would spend a term that has a precise meaning elsewhere.
    """
    row = item["lead"]
    steps = [
        {"label": "what stopped",
         "value": "the lead failed enough times to be parked rather than retried"},
        {"label": "kind", "value": row["kind"]},
        {"label": "attempts", "value": str(row["attempts"])},
    ]
    if row["platform"]:
        steps.append({"label": "platform", "value": row["platform"]})
    if row["last_error"]:
        steps.append({"label": "last error", "value": row["last_error"]})
    steps.append({
        "label": "why a person",
        "value": "the cause is outside the fleet — a missing credential, a disabled "
                 "source — and no amount of backoff removes it"})
    return steps


def today_item(item: dict[str, Any]) -> dict[str, Any]:
    """One thing asking something of a person.

    `actions` carries only what this API can actually perform. A parked lead therefore
    has none: `fleet.expedite` is what "run it now" would mean and no endpoint exposes
    it, so listing the action would be offering a control that posts nowhere. The item
    still carries its full reasoning, which is what makes it worth showing at all.

    `refused_because` on a candidate is the server saying in advance that Accept would
    be declined. It is absent (null) far more often than not, and that is correct rather
    than disappointing — see the note in `docs/reference/api-v1.md` about why the
    double-approve case can never appear here.
    """
    out = {
        "id": item["id"],
        "kind": item["kind"],
        "tone": item["tone"],
        "head": item["head"],
        "sub": item["sub"],
        "subject": {"kind": item["subject_kind"], "id": item["subject_id"],
                    "name": item["subject_name"]},
    }
    if item["kind"] == "suggestion_group":
        out["why"] = _why_suggestion_group(item)
        out["candidates"] = [suggestion(r) for r in item["suggestions"]]
        out["actions"] = [
            {"key": "accept", "label": "Accept", "style": "primary",
             "endpoint": "/api/v1/suggestions/{id}/accept", "per": "candidate"},
            {"key": "reject", "label": "Reject", "style": "danger",
             "endpoint": "/api/v1/suggestions/{id}/reject", "per": "candidate"},
        ]
    else:
        out["why"] = _why_parked_lead(item)
        out["candidates"] = []
        out["actions"] = []
        out["lead"] = pick(item["lead"], "id", "kind", "platform", "attempts",
                           "last_error", "party_name")
    return out
