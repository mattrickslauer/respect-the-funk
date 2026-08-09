"""Wireframe fixtures for the console.

None of this is real and none of it is meant to be. It is the shape of the product
rendered at full density so the layout, the information hierarchy and the inspector
can be judged before the tables behind them exist.

**Every counterparty, outlet, quote and handle here is invented.** Nothing puts words
in a real publication's mouth or attaches a real person to a real artist — the drift
failure mode the Forager design §7c names is easy to demonstrate accidentally, and a
fixture file is a stupid place to do it. Artist names come from the live roster because
that costs nothing and makes the screens legible; everything hanging off them is fiction.

The shapes are not fiction. Column names, provenance classes, lead kinds, thread states,
lease fields and budget units are the ones in
`docs/superpowers/specs/2026-08-07-agent-contract-design.md` and
`docs/PLATFORM-SPEC.md`, so a view built against these fixtures is built against the
eventual query. When the tables land, `routes` swaps the fixture for a `repo` call and
the templates do not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- navigation

#: (group, [(key, label, href, badge)]). Four groups because the operator arrives in
#: one of four moods: something needs me, what do we know, what are we running, is it
#: healthy. Nav that mirrors the mood is faster than nav that mirrors the schema.
NAV: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    ("Work", (
        ("today",     "Today",          "/",               ""),
        ("approvals", "Approvals",      "/approvals",      "3"),
        ("inbox",     "Inbox",          "/inbox",          "7"),
    )),
    ("Knowledge", (
        ("artists",   "Artists",        "/artists",        ""),
        ("tracks",    "Tracks",         "/tracks",         ""),
        ("facts",     "Facts",          "/facts",          ""),
        ("counter",   "Counterparties", "/counterparties", ""),
    )),
    ("Campaigns", (
        ("campaigns", "Campaigns",      "/campaigns",      ""),
        ("threads",   "Threads",        "/threads",        ""),
    )),
    ("System", (
        ("fleet",     "Fleet",          "/fleet",          ""),
        ("queue",     "Queue",          "/queue",          ""),
        ("runs",      "Runs & errors",  "/runs",           "4"),
        ("budgets",   "Budgets",        "/budgets",        "!"),
    )),
)

#: The scope switcher. Renders `scope_kind` from the contract design §7 as a control:
#: tenant-wide work genuinely has no artist, so "All artists" is a real scope and not a
#: filter that happens to be empty.
SCOPES: tuple[tuple[str, str, str], ...] = (
    ("all",           "All artists",   "tenant"),
    ("hallow-youth",  "Hallow Youth",  "artist"),
    ("amanda-kurt",   "Amanda Kurt",   "artist"),
    ("just-one-branch", "Just One Branch", "artist"),
)


# -------------------------------------------------------------- view plumbing

@dataclass(frozen=True)
class Col:
    """One column. `cls` drives alignment and typeface, not colour."""
    key: str
    label: str
    cls: str = ""
    width: str = ""


@dataclass(frozen=True)
class Field:
    """One input in a `form` section.

    Deliberately not a Pydantic model or a WTForms field: this describes what to
    *render*, and the server validates the post independently against `domain`.
    A field descriptor that also claimed to validate would invite trusting it, and
    the browser is not where the closed sets are enforced.
    """
    name: str
    label: str
    kind: str = "text"                  # text | url | select | static
    value: str = ""
    #: Grouped `((group, ((value, label), ...)), ...)`. A group of "" renders with no
    #: <optgroup>, so a flat select and a grouped one are the same structure.
    options: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    hint: str = ""
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class Section:
    """One block in the inspector.

    `kind` decides rendering: `kv` is a definition list, `chain` is the provenance
    walk with its indent rails, `quote` is verbatim evidence, `note` is prose,
    `actions` is a row of buttons, `form` is an editor that posts. The inspector is
    the only place in the product where an operator can act on a single object, so
    the actions — and now the writes — belong to it.
    """
    title: str
    kind: str
    items: tuple[Any, ...] = ()
    #: `form` only. Where it posts, what the submit button says, and whether it is
    #: styled as destructive. Empty on every other kind.
    action: str = ""
    submit: str = "Save"
    tone: str = ""                      # "" | "danger"
    #: `form` only. Rendered above the fields when the last post was rejected, so the
    #: operator sees why next to what they typed rather than on a separate error page.
    error: str = ""


@dataclass(frozen=True)
class View:
    key: str
    title: str
    blurb: str
    stats: tuple[tuple[str, str, str], ...]      # (label, value, bar-or-spark)
    cols: tuple[Col, ...]
    rows: tuple[dict[str, Any], ...]
    empty: str = "Nothing here."
    dense: bool = True


def _bar(pct: int) -> str:
    """A ten-cell bar. Rendered as text so it copies out of the page intact and
    needs no chart library in a Lambda that ships as a 24MB zip."""
    filled = round(pct / 10)
    return "█" * filled + "░" * (10 - filled)


# --------------------------------------------------------------------- facts

_FACT_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "f1", "dimension": "audience.city", "value": "Berlin",
        "prov": "measured", "glyph": "●", "conf": "0.91", "bar": _bar(91),
        "status": "live", "agent": "distiller", "when": "2026-08-04",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "audience.city"), ("value", "Berlin"),
                ("provenance", "measured"), ("status", "live"),
                ("confidence", "0.91"), ("observed", "2026-08-04 09:12Z"),
                ("written by", "distiller"), ("model", "claude-sonnet-5"),
            )),
            Section("Stands on", "chain", (
                (0, "chunk", "#4471", "party_chunk"),
                (1, "quote", "“…the trio, based in Berlin, have spent two years "
                             "building a live reputation before recording anything…”", ""),
                (0, "document", "example-press.test/hallow-youth-live", "party_document"),
                (1, "lead", "#221 · depth 1 · web_page", "lead"),
                (2, "lead", "#204 · depth 0 · web_search “Hallow Youth”", "lead"),
            )),
            Section("Supports", "chain", (
                (0, "fact", "audience.market_priority = DE/EU", "party_fact"),
                (0, "model", "artist_audience v3", "artist_audience"),
                (0, "lesson", "“open with the Berlin live reputation”", "lesson"),
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Retract", "Recheck")),
        ),
    },
    {
        "id": "f2", "dimension": "audience.age_band", "value": "18–24",
        "prov": "inferred", "glyph": "○", "conf": "0.62", "bar": _bar(62),
        "status": "live", "agent": "distiller", "when": "2026-08-05",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "audience.age_band"), ("value", "18–24 (est. 54%)"),
                ("provenance", "inferred"), ("status", "live"),
                ("confidence", "0.62"), ("error bar", "±11pp"),
                ("sample", "9 posts"), ("observed", "2026-08-05 22:40Z"),
            )),
            Section("Stands on", "chain", (
                (0, "metric", "instagram · median_comment_age · 9 posts", "party_metric"),
                (0, "fact", "audience.city = Berlin", "party_fact"),
                (0, "observation", "vendor demographic share", "counterparty_observation"),
            )),
            Section("Caution", "note", (
                "Inferred and low confidence. It may never overwrite a measured value, and "
                "the interface renders it differently for that reason — SCOPE-RESET §2a "
                "rule 1.",
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Retract", "Recheck")),
        ),
    },
    {
        "id": "f3", "dimension": "rights.master", "value": "Self-owned",
        "prov": "asserted", "glyph": "◆", "conf": "1.00", "bar": _bar(100),
        "status": "conflict", "agent": "operator", "when": "2026-07-30",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "rights.master"), ("value", "Self-owned"),
                ("provenance", "asserted"), ("status", "contradicted"),
                ("asserted by", "operator"), ("asserted", "2026-07-30 11:02Z"),
            )),
            Section("Conflicts with", "chain", (
                (0, "fact", "rights.master = “licensed to a label” · measured", "party_fact"),
                (1, "document", "example-registry.test/works/2214", "party_document"),
            )),
            Section("Why nothing was decided", "note", (
                "A human assertion and a crawled measurement disagree. The system raises a "
                "contradiction and stops rather than picking a winner — asserted and measured "
                "are different kinds of truth and neither outranks the other automatically. "
                "Rights are exactly where a silent auto-resolve costs real money.",
            )),
            Section("", "actions", ("Keep mine", "Accept crawl", "Both wrong", "Open source")),
        ),
    },
    {
        "id": "f4", "dimension": "sound.bpm", "value": "142",
        "prov": "measured", "glyph": "●", "conf": "1.00", "bar": _bar(100),
        "status": "live", "agent": "analyser", "when": "2026-07-28",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "sound.bpm"), ("value", "142"),
                ("provenance", "measured"), ("status", "live"),
                ("method", "essentia · rhythm_extractor"), ("tool", "v2.1-beta6"),
                ("track", "Cold Open"), ("measured", "2026-07-28 16:44Z"),
            )),
            Section("Stands on", "chain", (
                (0, "artefact", "master.wav · sha256 9f2c…41ab", "track"),
            )),
            Section("Note", "note", (
                "Permanent. A measurement carries no confidence column because it does not "
                "have one — it lives in a different table from the estimates for that reason.",
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Re-measure")),
        ),
    },
    {
        "id": "f5", "dimension": "press.angle", "value": "post-punk revival",
        "prov": "inferred", "glyph": "○", "conf": "0.44", "bar": _bar(44),
        "status": "stale", "agent": "distiller", "when": "2026-06-11",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "press.angle"), ("value", "post-punk revival"),
                ("provenance", "inferred"), ("status", "stale"),
                ("confidence", "0.44"), ("stale since", "2026-08-05 22:41Z"),
            )),
            Section("Why it went stale", "note", (
                "Its basis was retracted. The invalidator walked down from the retracted "
                "fact and marked this and six others — it marks, it never deletes, because a "
                "purge destroys the audit trail you need when a campaign goes sideways.",
            )),
            Section("Stood on", "chain", (
                (0, "fact", "catalogue.genre = post-punk · retracted", "party_fact"),
                (1, "document", "example-blog.test/scene-report", "party_document"),
            )),
            Section("", "actions", ("Recheck now", "Retract", "Keep anyway")),
        ),
    },
    {
        "id": "f6", "dimension": "socials.instagram", "value": "@example_handle",
        "prov": "measured", "glyph": "●", "conf": "1.00", "bar": _bar(100),
        "status": "live", "agent": "forager", "when": "2026-08-06",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "socials.instagram"), ("value", "@example_handle"),
                ("provenance", "measured"), ("status", "live"),
                ("mode", "owned"), ("verified", "graph api · standard access"),
            )),
            Section("Stands on", "chain", (
                (0, "profile", "instagram · owned · credential #2", "presence"),
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Recheck")),
        ),
    },
    {
        "id": "f7", "dimension": "catalogue.releases", "value": "4",
        "prov": "measured", "glyph": "●", "conf": "1.00", "bar": _bar(100),
        "status": "live", "agent": "forager", "when": "2026-08-04",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "catalogue.releases"), ("value", "4"),
                ("provenance", "measured"), ("status", "live"),
                ("source", "musicbrainz"), ("anchored on", "MBID"),
            )),
            Section("Stands on", "chain", (
                (0, "document", "musicbrainz · artist release-group list", "party_document"),
                (1, "identifier", "MBID · canonical", "party_identifier"),
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Recheck")),
        ),
    },
    {
        "id": "f8", "dimension": "live.territory", "value": "DE, NL, BE",
        "prov": "inferred", "glyph": "○", "conf": "0.71", "bar": _bar(71),
        "status": "live", "agent": "distiller", "when": "2026-08-06",
        "insp": (
            Section("Claim", "kv", (
                ("dimension", "live.territory"), ("value", "DE, NL, BE"),
                ("provenance", "inferred"), ("status", "live"),
                ("confidence", "0.71"), ("sample", "11 listings"),
            )),
            Section("Stands on", "chain", (
                (0, "document", "example-listings.test/hallow-youth", "party_document"),
                (0, "fact", "audience.city = Berlin", "party_fact"),
            )),
            Section("", "actions", ("Relevant", "Not relevant", "Recheck")),
        ),
    },
)

FACTS = View(
    key="facts", title="Facts",
    blurb="Everything the fleet believes, and what each belief stands on. "
          "Measured, inferred and asserted are stored differently on purpose.",
    stats=(("claims", "847", ""), ("live", "802", _bar(95)),
           ("stale", "38", _bar(4)), ("contradicted", "7", _bar(1)),
           ("new / 1h", "▲ 23", "▁▂▃▃▅▆█")),
    cols=(Col("dimension", "Dimension", "b", "24%"), Col("value", "Value", "", "22%"),
          Col("prov", "Prov", "prov", "10%"), Col("conf", "Conf", "num", "6%"),
          Col("bar", "", "bar", "12%"), Col("status", "Status", "chip", "10%"),
          Col("agent", "By", "mono", "10%"), Col("when", "Seen", "mono", "10%")),
    rows=_FACT_ROWS,
)


# --------------------------------------------------------------------- queue

_QUEUE_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "q1", "lead": "#2214", "kind": "engagement", "target": "instagram · owned · comments",
        "depth": "1", "score": "0.86", "bar": _bar(86), "state": "claimed",
        "owner": "forager-2", "lease": "4m12s", "next": "now",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2214"), ("kind", "engagement"), ("scope", "artist · Hallow Youth"),
                ("state", "claimed"), ("owner", "forager-2"),
                ("lease expires", "in 4m 12s"), ("attempts", "0"),
                ("score", "0.86"), ("depth", "1"), ("cadence", "6h · recurring"),
            )),
            Section("Why we are looking here", "chain", (
                (0, "lead", "#2214 · engagement · owned profile comments", "lead"),
                (1, "lead", "#2201 · profile · instagram owned", "lead"),
                (2, "lead", "#2180 · seed · presence mode=owned", "lead"),
            )),
            Section("Note", "note", (
                "The attention trail is a recursive walk up parent_lead_id. For any document, "
                "fact or contact the system holds, it answers “why were we even looking here?” "
                "— which is the provenance record and the compliance artifact at once.",
            )),
            Section("", "actions", ("Release lease", "Reject subtree", "Boost", "Open target")),
        ),
    },
    {
        "id": "q2", "lead": "#2209", "kind": "mention_search", "target": "web_search · “Hallow Youth” review",
        "depth": "0", "score": "0.74", "bar": _bar(74), "state": "failed",
        "owner": "—", "lease": "—", "next": "retry 3 · 8m",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2209"), ("kind", "mention_search"), ("state", "failed"),
                ("attempts", "2 of 5"), ("last error", "429 rate limited"),
                ("backoff", "8m → 30m → 2h"), ("score", "0.74"),
            )),
            Section("Last error", "quote", (
                "HTTP 429 from search adapter · retry-after 480s · "
                "adapter=web_search request=01K9…7Q2",
            )),
            Section("Note", "note", (
                "A failed lead is rescheduled by its backoff ladder, not dropped. After the "
                "last attempt it stays as a row with its error, because a lead that vanished "
                "and a lead that failed look identical in a queue that deletes.",
            )),
            Section("", "actions", ("Retry now", "Skip", "Reject subtree")),
        ),
    },
    {
        "id": "q3", "lead": "#2231", "kind": "fan_artifact", "target": "tiktok · sound page · manual",
        "depth": "2", "score": "0.41", "bar": _bar(41), "state": "blocked_manual",
        "owner": "—", "lease": "—", "next": "awaiting human",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2231"), ("kind", "fan_artifact"), ("mode", "manual"),
                ("state", "blocked_manual"), ("platform", "tiktok"),
                ("score", "0.41"), ("depth", "2"),
            )),
            Section("Why a human", "note", (
                "There is no compliant API for “who used this sound”, and the research verdict "
                "is no scraper, ever. Manual sound-page browsing is both compliant and the "
                "highest-signal path — so the queue holds the task and asks, rather than "
                "quietly doing something we decided not to do.",
            )),
            Section("", "actions", ("Open sound page", "Record findings", "Skip")),
        ),
    },
    {
        "id": "q4", "lead": "#2240", "kind": "metric", "target": "spotify · monthly listeners",
        "depth": "0", "score": "0.90", "bar": _bar(90), "state": "pending",
        "owner": "—", "lease": "—", "next": "in 51m",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2240"), ("kind", "metric"), ("state", "pending"),
                ("cadence", "24h · recurring"), ("next action", "in 51m"),
                ("score", "0.90"),
            )),
            Section("Note", "note", (
                "Routine polling and open-ended discovery ride the same table, the same claim "
                "query and the same worker. One nullable cadence column is the whole "
                "difference between a crawler and a frontier.",
            )),
            Section("", "actions", ("Run now", "Change cadence", "Pause")),
        ),
    },
    {
        "id": "q5", "lead": "#2246", "kind": "gap_query", "target": "web_search · rights / publishing",
        "depth": "0", "score": "0.68", "bar": _bar(68), "state": "pending",
        "owner": "—", "lease": "—", "next": "in 2m",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2246"), ("kind", "gap_query"), ("state", "pending"),
                ("raised by", "coverage evaluation"), ("score", "0.68"),
            )),
            Section("Why it exists", "note", (
                "The rights dimension is thin for this artist, so the system queued work "
                "against its own blind spot. Gap queries are how the frontier notices what it "
                "does not know rather than only following what it found.",
            )),
            Section("", "actions", ("Run now", "Reject", "Boost")),
        ),
    },
    {
        "id": "q6", "lead": "#2251", "kind": "entity", "target": "counterparty candidate · creator",
        "depth": "3", "score": "0.29", "bar": _bar(29), "state": "rejected",
        "owner": "—", "lease": "—", "next": "—",
        "insp": (
            Section("Lead", "kv", (
                ("id", "#2251"), ("kind", "entity"), ("state", "rejected"),
                ("depth", "3"), ("score", "0.29"), ("rejected by", "score floor"),
            )),
            Section("Why rejected", "note", (
                "Depth decay is 0.6ⁿ, so three hops out starts at a fifth of the weight, and "
                "this one reached no canonical identifier. Past depth 2 an unanchored lead is "
                "rejected outright — drift is the real failure mode, not volume.",
            )),
            Section("", "actions", ("Force run", "Mark relevant")),
        ),
    },
)

QUEUE = View(
    key="queue", title="Queue",
    blurb="The frontier. Every lead scored, leased, deduped, and remembering the lead "
          "that produced it.",
    stats=(("pending", "1,284", ""), ("claimed", "9", _bar(12)),
           ("failed", "11", _bar(6)), ("blocked", "3", ""),
           ("throughput", "212/h", "▃▅▄▆█▇▆")),
    cols=(Col("lead", "Lead", "mono b", "8%"), Col("kind", "Kind", "chip", "13%"),
          Col("target", "Target", "", "30%"), Col("depth", "D", "num", "4%"),
          Col("score", "Score", "num", "7%"), Col("bar", "", "bar", "10%"),
          Col("state", "State", "chip", "12%"), Col("next", "Next", "mono", "12%")),
    rows=_QUEUE_ROWS,
)


# ----------------------------------------------------------------- the fleet

_FLEET_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "a1", "agent": "forager", "state": "working", "leased": "6",
        "bar": _bar(60), "scope": "artist, track", "work": "lead",
        "rate": "212/h", "spark": "▃▅▄▆█▇▆", "errors": "1",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "forager"), ("work_table", "lead"), ("claim_state", "pending"),
                ("scope_kinds", "artist, track"), ("concurrency", "4"),
                ("per artist cap", "2"), ("batch", "12"), ("lease", "300s"),
                ("max attempts", "5"), ("enabled", "true"),
            )),
            Section("Adapters", "kv", (
                ("spotify", "ok · 118ms p50"), ("musicbrainz", "ok · 240ms p50"),
                ("web_search", "degraded · 429s"), ("web_page", "ok · 610ms p50"),
                ("instagram_owned", "ok · 190ms p50"),
            )),
            Section("Writes", "kv", (
                ("party_fact", "declared"), ("party_metric", "declared"),
                ("party_document", "declared"),
            )),
            Section("Note", "note", (
                "`writes` is a declared blast radius, not documentation. The runtime refuses a "
                "handler writing outside it — so when something wrong shows up in the store, "
                "this list already narrows who could have put it there.",
            )),
            Section("", "actions", ("Disable", "Drain", "Edit manifest", "View runs")),
        ),
    },
    {
        "id": "a2", "agent": "distiller", "state": "working", "leased": "2",
        "bar": _bar(20), "scope": "artist, track", "work": "chunk_staging",
        "rate": "88/h", "spark": "▂▃▅▄▃▄▅", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "distiller"), ("work_table", "chunk_staging"),
                ("scope_kinds", "artist, track"), ("adapters", "none"),
                ("woken by", "party_chunk insert"), ("enabled", "true"),
            )),
            Section("Writes", "kv", (("party_fact", "declared"), ("fact_basis", "declared"))),
            Section("Note", "note", (
                "No adapters at all — it never touches the network. It reads chunks and writes "
                "claims with the basis edges under them, and the runtime rejects any inferred "
                "fact that cites nothing.",
            )),
            Section("", "actions", ("Disable", "Drain", "Edit manifest", "View runs")),
        ),
    },
    {
        "id": "a3", "agent": "invalidator", "state": "idle", "leased": "0",
        "bar": _bar(0), "scope": "tenant, artist, track", "work": "fact_basis",
        "rate": "3/h", "spark": "▁▁▁█▁▁▁", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "invalidator"), ("work_table", "fact_basis"),
                ("scope_kinds", "all three"), ("woken by", "any fact status flip"),
                ("depth cap", "8"), ("enabled", "true"),
            )),
            Section("Last cascade", "chain", (
                (0, "retracted", "catalogue.genre = post-punk", "party_fact"),
                (1, "stale", "press.angle = post-punk revival", "party_fact"),
                (1, "stale", "audience.scene_affinity", "party_fact"),
                (2, "stale", "lesson “open with the scene angle”", "lesson"),
            )),
            Section("Note", "note", (
                "Idle is the correct state most of the time. It spikes when a human retracts "
                "something, and the spike is the whole point — one correction reaching every "
                "conclusion built on it, with no cross-agent code.",
            )),
            Section("", "actions", ("Disable", "Replay last", "View runs")),
        ),
    },
    {
        "id": "a4", "agent": "scout", "state": "idle", "leased": "0",
        "bar": _bar(0), "scope": "tenant", "work": "lead",
        "rate": "12/h", "spark": "▁▂▁▁▃▁▁", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "scout"), ("work_table", "lead"), ("scope_kinds", "tenant"),
                ("adapters", "web_search, spotify, youtube, manual"),
                ("cadence", "label-wide sweep · 6h"), ("enabled", "true"),
            )),
            Section("Note", "note", (
                "The only tenant-scoped agent. A creator index belongs to the label, not to an "
                "artist — which is why work carries a scope tier and the fairness claim picks a "
                "bucket rather than an artist, so a launch week cannot starve the sweep.",
            )),
            Section("", "actions", ("Disable", "Run sweep now", "Edit manifest")),
        ),
    },
    {
        "id": "a5", "agent": "drafter", "state": "working", "leased": "1",
        "bar": _bar(10), "scope": "track", "work": "thread",
        "rate": "9/h", "spark": "▁▂▃▂▁▂▃", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "drafter"), ("work_table", "thread"),
                ("claim_state", "approved"), ("scope_kinds", "track"),
                ("requires_human", "true"), ("enabled", "true"),
            )),
            Section("Note", "note", (
                "Writes a draft and sets the thread to awaiting_human. It never sends — that is "
                "the sender's job, and the split is what makes the irreversible act "
                "single-purpose and auditable.",
            )),
            Section("", "actions", ("Disable", "Drain", "Edit manifest")),
        ),
    },
    {
        "id": "a6", "agent": "sender", "state": "idle", "leased": "0",
        "bar": _bar(0), "scope": "track", "work": "outbox",
        "rate": "6/h", "spark": "▁▁▂▁▁▁▂", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "sender"), ("work_table", "outbox"),
                ("adapters", "email"), ("requires_human", "true"),
                ("idempotency", "provider key per message"), ("enabled", "true"),
            )),
            Section("Note", "note", (
                "The message row and the outbox row are written in one transaction. A crash "
                "between claim and send retries against the same key and the provider "
                "deduplicates — you burn a relationship exactly once.",
            )),
            Section("", "actions", ("Disable", "Pause sending", "View outbox")),
        ),
    },
    {
        "id": "a7", "agent": "inbox", "state": "working", "leased": "1",
        "bar": _bar(10), "scope": "track", "work": "message",
        "rate": "4/h", "spark": "▁▂▁▃▂▁▂", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "inbox"), ("work_table", "message"),
                ("claim_state", "inbound unclassified"), ("scope_kinds", "track"),
                ("enabled", "true"),
            )),
            Section("Note", "note", (
                "Rung 1 of the feedback ladder starts here. A reply is fast, clean and "
                "uncontaminated — which is why it trains the scorer and stream count never does.",
            )),
            Section("", "actions", ("Disable", "Drain", "View classifications")),
        ),
    },
    {
        "id": "a8", "agent": "researcher", "state": "off", "leased": "0",
        "bar": _bar(0), "scope": "tenant", "work": "thread",
        "rate": "—", "spark": "▁▁▁▁▁▁▁", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (
                ("kind", "researcher"), ("enabled", "false"),
                ("deferred", "before the build started"),
            )),
            Section("Note", "note", (
                "Off with an UPDATE, not a deploy. Combined with the lease that is a clean "
                "drain: it stops claiming, finishes what it holds, and goes quiet.",
            )),
            Section("", "actions", ("Enable", "Edit manifest")),
        ),
    },
    {
        "id": "a9", "agent": "negotiator", "state": "off", "leased": "0",
        "bar": _bar(0), "scope": "track", "work": "thread",
        "rate": "—", "spark": "▁▁▁▁▁▁▁", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (("kind", "negotiator"), ("enabled", "false"),
                                       ("deferred", "cut order, first out"))),
            Section("", "actions", ("Enable", "Edit manifest")),
        ),
    },
    {
        "id": "a10", "agent": "analyst", "state": "off", "leased": "0",
        "bar": _bar(0), "scope": "artist", "work": "thread",
        "rate": "—", "spark": "▁▁▁▁▁▁▁", "errors": "0",
        "insp": (
            Section("Manifest", "kv", (("kind", "analyst"), ("enabled", "false"),
                                       ("deferred", "cut order, second out"))),
            Section("", "actions", ("Enable", "Edit manifest")),
        ),
    },
)

FLEET = View(
    key="fleet", title="Fleet",
    blurb="Ten agents, no orchestrator. Each claims work by lease, acts, writes back, "
          "and that write wakes the next one.",
    stats=(("agents", "10", ""), ("working", "4", _bar(40)),
           ("idle", "3", ""), ("off", "3", ""), ("errors / 1h", "1", "▁▁▁▂▁▁▁")),
    cols=(Col("agent", "Agent", "b", "14%"), Col("state", "State", "chip", "10%"),
          Col("leased", "Leased", "num", "7%"), Col("bar", "", "bar", "11%"),
          Col("work", "Claims from", "mono", "16%"), Col("scope", "Scope", "mono", "16%"),
          Col("rate", "Rate", "num", "8%"), Col("spark", "1h", "spark", "8%"),
          Col("errors", "Err", "num", "5%")),
    rows=_FLEET_ROWS,
)


# ------------------------------------------------------------------- budgets

_BUDGET_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "b1", "artist": "Hallow Youth", "scope": "artist", "spent": "18.2k",
        "cap": "20k", "pct": "94%", "bar": _bar(94), "state": "near cap",
        "docs": "412", "leads": "1,104", "cost": "$2.41",
        "insp": (
            Section("Budget", "kv", (
                ("scope", "artist · Hallow Youth"), ("window", "rolling 24h"),
                ("tokens", "18,204 of 20,000"), ("documents", "412 of 500"),
                ("leads", "1,104 of 1,500"), ("max depth", "3"),
                ("est. cost", "$2.41"), ("resets", "00:00 UTC"),
            )),
            Section("What was dropped", "kv", (
                ("leads refused", "37"), ("reason", "per-run cap"),
                ("recorded in", "agent_run"),
            )),
            Section("Note", "note", (
                "The governor writes what it dropped rather than truncating silently. Silent "
                "truncation reads as “we covered everything” when it did not — and spend is "
                "summed from agent_run rather than decremented from a counter, so ten workers "
                "on one launching artist do not serialise on a single hot row.",
            )),
            Section("", "actions", ("Raise cap", "Pause artist", "View dropped", "View runs")),
        ),
    },
    {
        "id": "b2", "artist": "Amanda Kurt", "scope": "artist", "spent": "4.1k",
        "cap": "20k", "pct": "21%", "bar": _bar(21), "state": "ok",
        "docs": "96", "leads": "220", "cost": "$0.54",
        "insp": (
            Section("Budget", "kv", (
                ("scope", "artist · Amanda Kurt"), ("tokens", "4,118 of 20,000"),
                ("documents", "96 of 500"), ("leads", "220 of 1,500"),
                ("est. cost", "$0.54"),
            )),
            Section("", "actions", ("Raise cap", "Pause artist", "View runs")),
        ),
    },
    {
        "id": "b3", "artist": "Just One Branch", "scope": "artist", "spent": "0",
        "cap": "20k", "pct": "0%", "bar": _bar(0), "state": "paused",
        "docs": "0", "leads": "0", "cost": "$0.00",
        "insp": (
            Section("Budget", "kv", (
                ("scope", "artist · Just One Branch"), ("state", "paused by operator"),
                ("tokens", "0 of 20,000"),
            )),
            Section("", "actions", ("Resume", "Raise cap")),
        ),
    },
    {
        "id": "b4", "artist": "Label-wide sweep", "scope": "tenant", "spent": "6.9k",
        "cap": "15k", "pct": "46%", "bar": _bar(46), "state": "ok",
        "docs": "301", "leads": "780", "cost": "$0.91",
        "insp": (
            Section("Budget", "kv", (
                ("scope", "tenant · no artist"), ("tokens", "6,903 of 15,000"),
                ("counterparties found", "142"), ("est. cost", "$0.91"),
            )),
            Section("Note", "note", (
                "Tenant-scoped work has its own bucket in the fairness claim, so a launch week "
                "cannot starve the creator sweep and the sweep cannot starve a launch.",
            )),
            Section("", "actions", ("Raise cap", "Pause sweep", "View runs")),
        ),
    },
    {
        "id": "b5", "artist": "CockroachDB", "scope": "infra", "spent": "—",
        "cap": "—", "pct": "31%", "bar": _bar(31), "state": "ok",
        "docs": "—", "leads": "—", "cost": "$0.00",
        "insp": (
            Section("Cluster", "kv", (
                ("cluster", "respect-the-funk-31317"), ("tier", "Basic · scales to zero"),
                ("request units", "31% of free allowance"), ("idle cost", "$0.00"),
                ("vector index", "enabled"), ("changefeeds", "0 running"),
            )),
            Section("Unverified", "note", (
                "RU cost of a filtered vector scan and changefeed RU draw are both still "
                "unmeasured — they need real row volume, and a probe against an empty table "
                "measures nothing. Marked rather than guessed.",
            )),
            Section("", "actions", ("Open cluster", "Run RU probe")),
        ),
    },
)

BUDGETS = View(
    key="budgets", title="Budgets",
    blurb="What each scope is allowed to spend, what it spent, and what it dropped "
          "when it ran out.",
    stats=(("spend today", "$4.86", ""), ("tokens", "29.2k", _bar(52)),
           ("near cap", "1", ""), ("paused", "1", ""), ("idle db cost", "$0.00", "")),
    cols=(Col("artist", "Scope", "b", "20%"), Col("scope", "Tier", "chip", "9%"),
          Col("spent", "Spent", "num", "9%"), Col("cap", "Cap", "num", "8%"),
          Col("bar", "", "bar", "14%"), Col("pct", "%", "num", "6%"),
          Col("leads", "Leads", "num", "9%"), Col("cost", "Cost", "num", "9%"),
          Col("state", "State", "chip", "12%")),
    rows=_BUDGET_ROWS,
)


# --------------------------------------------------------------- runs, errors

_RUN_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "r1", "at": "14:22:07", "agent": "forager", "what": "fetched example-press.test/hallow-youth-live",
        "dur": "1.2s", "tok": "1,204", "result": "ok", "state": "ok",
        "insp": (
            Section("Run", "kv", (
                ("id", "01K9F2…7Q2"), ("agent", "forager"), ("lead", "#2211"),
                ("started", "14:22:05.9Z"), ("duration", "1.24s"),
                ("tokens in / out", "980 / 224"), ("cost", "$0.004"),
                ("state", "ok"),
            )),
            Section("Produced", "kv", (
                ("documents", "1"), ("chunks", "6"), ("metrics", "0"), ("new leads", "3"),
            )),
            Section("Note", "note", (
                "agent_run is not telemetry. It is what makes the fleet restartable and a "
                "decision explainable — and it is where spend is summed from.",
            )),
            Section("", "actions", ("Open lead", "Open document", "Replay")),
        ),
    },
    {
        "id": "r2", "at": "14:22:07", "agent": "distiller", "what": "+3 facts · audience.city, live.territory, catalogue.releases",
        "dur": "0.9s", "tok": "2,860", "result": "ok", "state": "ok",
        "insp": (
            Section("Run", "kv", (
                ("agent", "distiller"), ("chunks read", "6"), ("facts written", "3"),
                ("basis edges", "7"), ("duration", "0.91s"), ("state", "ok"),
            )),
            Section("", "actions", ("Open facts", "Replay")),
        ),
    },
    {
        "id": "r3", "at": "14:22:01", "agent": "invalidator", "what": "7 facts → stale · basis retracted",
        "dur": "0.3s", "tok": "0", "result": "ok", "state": "ok",
        "insp": (
            Section("Run", "kv", (
                ("agent", "invalidator"), ("trigger", "artist_fact status → retracted"),
                ("walked", "depth 3"), ("marked stale", "7"), ("deleted", "0"),
                ("duration", "0.31s"),
            )),
            Section("Cascade", "chain", (
                (0, "retracted", "catalogue.genre = post-punk", "party_fact"),
                (1, "stale", "press.angle", "party_fact"),
                (1, "stale", "audience.scene_affinity", "party_fact"),
                (2, "stale", "lesson “open with the scene angle”", "lesson"),
            )),
            Section("", "actions", ("Open cascade", "Undo retraction")),
        ),
    },
    {
        "id": "r4", "at": "14:21:40", "agent": "forager", "what": "lead #2209 failed · 429 rate limited",
        "dur": "0.4s", "tok": "0", "result": "429", "state": "error",
        "insp": (
            Section("Failure", "kv", (
                ("agent", "forager"), ("lead", "#2209"), ("adapter", "web_search"),
                ("http", "429"), ("attempt", "2 of 5"), ("retry in", "8m"),
                ("state", "failed · will retry"),
            )),
            Section("Error", "quote", (
                "web_search adapter: rate limited. retry-after 480s. "
                "request=01K9F2XR7Q2 provider=serp quota=daily",
            )),
            Section("Blast radius", "kv", (
                ("leads waiting on this", "4"), ("facts affected", "0"),
                ("threads affected", "0"),
            )),
            Section("", "actions", ("Retry now", "Open lead", "Disable adapter", "Silence 1h")),
        ),
    },
    {
        "id": "r5", "at": "14:19:12", "agent": "sender", "what": "sent → @example_creator · UGC · Cold Open",
        "dur": "0.7s", "tok": "0", "result": "ok", "state": "ok",
        "insp": (
            Section("Run", "kv", (
                ("agent", "sender"), ("thread", "#88"), ("provider id", "0199f2…a1"),
                ("idempotency key", "thr88-msg3-v1"), ("state", "sent"),
            )),
            Section("Note", "note", (
                "The only irreversible act in the system. It is claimed from the outbox, and "
                "the provider id is recorded against a key the sender already held — so a "
                "crash mid-send retries into a deduplicate rather than a second email.",
            )),
            Section("", "actions", ("Open thread", "View message")),
        ),
    },
    {
        "id": "r6", "at": "14:14:03", "agent": "distiller", "what": "contradiction raised · rights.master",
        "dur": "1.1s", "tok": "1,940", "result": "conflict", "state": "warn",
        "insp": (
            Section("Contradiction", "kv", (
                ("dimension", "rights.master"), ("asserted", "Self-owned · operator"),
                ("measured", "licensed to a label · registry"),
                ("state", "open · awaiting human"),
            )),
            Section("Note", "note", (
                "Nothing was decided, deliberately. Asserted and measured are different kinds "
                "of truth and neither outranks the other automatically, so the system raises it "
                "and stops.",
            )),
            Section("", "actions", ("Resolve", "Open both sources")),
        ),
    },
)

RUNS = View(
    key="runs", title="Runs & errors",
    blurb="Every action every agent took, what it cost, and what broke. Restartability "
          "and explainability come from the same rows.",
    stats=(("runs / 1h", "1,412", "▄▅▆█▇▆▅"), ("errors", "4", _bar(3)),
           ("p50", "0.9s", ""), ("p95", "3.4s", ""), ("tokens / 1h", "182k", "")),
    cols=(Col("at", "Time", "mono", "9%"), Col("agent", "Agent", "b", "11%"),
          Col("what", "What", "", "45%"), Col("dur", "Dur", "num", "7%"),
          Col("tok", "Tokens", "num", "9%"), Col("result", "Result", "chip", "9%")),
    rows=_RUN_ROWS,
)


# ------------------------------------------------------------ counterparties

_CP_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "c1", "who": "@example_creator", "kind": "creator", "platform": "tiktok",
        "reach": "84k", "bar": _bar(72), "fit": "0.82", "rel": "agreed",
        "last": "2h ago", "source": "manual · sound page",
        "insp": (
            Section("Counterparty", "kv", (
                ("handle", "@example_creator"), ("kind", "creator"),
                ("platform", "tiktok"), ("followers", "84,200 · measured"),
                ("median views", "21,400 · measured"), ("fit", "0.82 · inferred"),
                ("relationship", "agreed · 1 prior collaboration"),
            )),
            Section("How we got them", "chain", (
                (0, "contact", "business email · from public bio", "counterparty_contact"),
                (1, "lead", "#1980 · manual · sound page browse", "lead"),
                (2, "lead", "#1974 · fan_artifact · sound id", "lead"),
                (3, "lead", "#1960 · seed · track Cold Open", "lead"),
            )),
            Section("Note", "note", (
                "Business-inquiry contact from a public professional surface, with the exact "
                "chain of hops that surfaced them. That record is the provenance system and the "
                "answer to “where did you get my address” at the same time.",
            )),
            Section("", "actions", ("Open thread", "Suppress", "Mark do-not-contact")),
        ),
    },
    {
        "id": "c2", "who": "Example Radio Nova", "kind": "radio", "platform": "fm · DE",
        "reach": "—", "bar": _bar(55), "fit": "0.66", "rel": "negotiating",
        "last": "4h ago", "source": "station list · seed csv",
        "insp": (
            Section("Counterparty", "kv", (
                ("station", "Example Radio Nova"), ("kind", "radio"),
                ("territory", "DE"), ("programmer", "role: programmer"),
                ("relationship", "negotiating"), ("fit", "0.66 · inferred"),
            )),
            Section("Open question", "note", (
                "They asked for a rate. Nothing is sent from here without approval, and the "
                "negotiator is off, so this is waiting on you.",
            )),
            Section("", "actions", ("Open thread", "Draft reply", "Suppress")),
        ),
    },
    {
        "id": "c3", "who": "example-blog.test", "kind": "press", "platform": "web",
        "reach": "12k", "bar": _bar(31), "fit": "0.58", "rel": "replied",
        "last": "1d ago", "source": "press page",
        "insp": (
            Section("Counterparty", "kv", (
                ("outlet", "example-blog.test"), ("kind", "press"),
                ("monthly readers", "12,000 · inferred"), ("relationship", "replied"),
            )),
            Section("", "actions", ("Open thread", "Suppress")),
        ),
    },
    {
        "id": "c4", "who": "@example_curator", "kind": "curator", "platform": "spotify",
        "reach": "31k", "bar": _bar(44), "fit": "0.71", "rel": "shortlisted",
        "last": "—", "source": "playlist scrape · api",
        "insp": (
            Section("Counterparty", "kv", (
                ("curator", "@example_curator"), ("kind", "curator"),
                ("playlist followers", "31,000 · measured"),
                ("relationship", "shortlisted · never contacted"),
            )),
            Section("Blocked", "note", (
                "Cannot be worked right now: a thread for this counterparty is already open "
                "under another artist's campaign. One open thread per counterparty across the "
                "whole label — the second insert fails structurally rather than by convention.",
            )),
            Section("", "actions", ("View blocking thread", "Queue for later", "Suppress")),
        ),
    },
    {
        "id": "c5", "who": "@example_dnc", "kind": "creator", "platform": "instagram",
        "reach": "60k", "bar": _bar(60), "fit": "—", "rel": "do-not-contact",
        "last": "—", "source": "inbound · unsubscribe",
        "insp": (
            Section("Counterparty", "kv", (
                ("handle", "@example_dnc"), ("relationship", "do-not-contact"),
                ("suppressed", "2026-07-14"), ("reason", "unsubscribed"),
            )),
            Section("Note", "note", (
                "Suppression is permanent and survives everything — no campaign, no artist and "
                "no future sweep can reopen it.",
            )),
            Section("", "actions", ("View history",)),
        ),
    },
)

COUNTERPARTIES = View(
    key="counter", title="Counterparties",
    blurb="Everyone who can get the music out — creators, programmers, curators, press. "
          "A label asset, with the state of every relationship.",
    stats=(("indexed", "1,842", ""), ("contactable", "1,204", _bar(65)),
           ("open threads", "48", ""), ("agreed", "12", ""),
           ("suppressed", "31", "")),
    cols=(Col("who", "Who", "b", "20%"), Col("kind", "Kind", "chip", "10%"),
          Col("platform", "Platform", "mono", "12%"), Col("reach", "Reach", "num", "8%"),
          Col("bar", "", "bar", "10%"), Col("fit", "Fit", "num", "6%"),
          Col("rel", "Relationship", "chip", "14%"), Col("source", "Sourced", "mono", "20%")),
    rows=_CP_ROWS,
)


# ------------------------------------------------------------------- threads

_THREAD_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "t1", "who": "@example_creator", "channel": "ugc", "artist": "Hallow Youth",
        "track": "Cold Open", "state": "agreed", "bar": _bar(90), "age": "6d",
        "last": "2h ago", "holdout": "—",
        "insp": (
            Section("Thread", "kv", (
                ("counterparty", "@example_creator"), ("channel", "ugc"),
                ("campaign", "Cold Open · UGC"), ("state", "agreed"),
                ("messages", "5"), ("opened", "2026-08-01"), ("lease", "none"),
            )),
            Section("Conversation", "quote", (
                "→ pitch sent · 2026-08-01\n"
                "← replied “interested, what's the timeline?” · 2026-08-02\n"
                "→ timeline + rate · 2026-08-02\n"
                "← “works for me” · 2026-08-05\n"
                "→ assets delivered · 2026-08-06",
            )),
            Section("Next", "kv", (
                ("awaiting", "delivery verification"),
                ("verified by", "forager · lead #2260"),
                ("rung", "2 · delivery"),
            )),
            Section("", "actions", ("Open conversation", "Mark delivered", "Close")),
        ),
    },
    {
        "id": "t2", "who": "Example Radio Nova", "channel": "radio", "artist": "Hallow Youth",
        "track": "Cold Open", "state": "negotiating", "bar": _bar(60), "age": "3d",
        "last": "4h ago", "holdout": "—",
        "insp": (
            Section("Thread", "kv", (
                ("counterparty", "Example Radio Nova"), ("channel", "radio"),
                ("state", "negotiating"), ("messages", "3"),
                ("next action", "awaiting human"),
            )),
            Section("Needs you", "note", (
                "They asked for a rate. The negotiator is off, so this will sit until you "
                "answer it — which is the correct behaviour, not a stall.",
            )),
            Section("", "actions", ("Draft reply", "Open conversation", "Close as lost")),
        ),
    },
    {
        "id": "t3", "who": "example-blog.test", "channel": "press", "artist": "Amanda Kurt",
        "track": "Slow Exit", "state": "awaiting_reply", "bar": _bar(35), "age": "9d",
        "last": "9d ago", "holdout": "—",
        "insp": (
            Section("Thread", "kv", (
                ("state", "awaiting_reply"), ("sent", "9 days ago"),
                ("follow-up due", "in 5 days"),
            )),
            Section("", "actions", ("Follow up", "Close as no-reply")),
        ),
    },
    {
        "id": "t4", "who": "@example_holdout", "channel": "ugc", "artist": "Hallow Youth",
        "track": "Cold Open", "state": "holdout", "bar": _bar(0), "age": "6d",
        "last": "—", "holdout": "yes",
        "insp": (
            Section("Thread", "kv", (
                ("state", "holdout · deliberately not contacted"),
                ("cohort", "15 of 100 shortlisted"),
            )),
            Section("Why", "note", (
                "Shortlisted, matched, and deliberately left alone so there is something to "
                "compare against. It is the only causal claim available at this scale, and it "
                "costs nothing but the discipline to leave them alone.",
            )),
            Section("", "actions", ("Release into campaign", "View cohort")),
        ),
    },
)

THREADS = View(
    key="threads", title="Threads",
    blurb="Every conversation, and where it stands. One open thread per counterparty "
          "across the whole label — enforced by the database, not by convention.",
    stats=(("open", "48", ""), ("awaiting you", "2", _bar(4)),
           ("awaiting them", "23", ""), ("agreed", "12", _bar(25)),
           ("holdout", "15", "")),
    cols=(Col("who", "Counterparty", "b", "20%"), Col("channel", "Channel", "chip", "9%"),
          Col("artist", "Artist", "", "14%"), Col("track", "Track", "", "12%"),
          Col("state", "State", "chip", "14%"), Col("bar", "", "bar", "10%"),
          Col("age", "Age", "mono", "6%"), Col("last", "Last", "mono", "9%")),
    rows=_THREAD_ROWS,
)


# -------------------------------------------------------------------- tracks

_TRACK_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "k1", "title": "Cold Open", "artist": "Hallow Youth", "state": "launch",
        "bpm": "142", "key": "F♯ min", "analysed": "yes", "campaigns": "2",
        "streams": "41.2k", "spark": "▁▂▂▃▅▆█",
        "insp": (
            Section("Track", "kv", (
                ("title", "Cold Open"), ("artist", "Hallow Youth"),
                ("isrc", "—"), ("released", "2026-07-24"),
                ("window", "launch · day 14"),
            )),
            Section("Measured", "kv", (
                ("bpm", "142"), ("key", "F♯ minor"), ("duration", "3:28"),
                ("drop", "0:47"), ("hook", "0:47 – 1:04"),
            )),
            Section("Inferred", "kv", (
                ("mood", "urgent, cold"), ("era", "late-70s reference"),
                ("confidence", "0.68 · v3"),
            )),
            Section("Asserted", "kv", (
                ("master", "contradicted — see facts"), ("splits", "not recorded"),
            )),
            Section("", "actions", ("Open campaigns", "Re-analyse", "Add rights")),
        ),
    },
    {
        "id": "k2", "title": "Slow Exit", "artist": "Amanda Kurt", "state": "active",
        "bpm": "96", "key": "C maj", "analysed": "yes", "campaigns": "1",
        "streams": "8.4k", "spark": "▃▃▂▂▂▁▁",
        "insp": (
            Section("Track", "kv", (
                ("title", "Slow Exit"), ("artist", "Amanda Kurt"),
                ("released", "2026-05-02"), ("window", "catalogue"),
            )),
            Section("Measured", "kv", (("bpm", "96"), ("key", "C major"), ("duration", "4:02"))),
            Section("Note", "note", (
                "Out of its launch window, so it gets catalogue crawl budget rather than "
                "launch budget. The artist is where knowledge compounds; the track is where "
                "the fleet spends.",
            )),
            Section("", "actions", ("Open campaign", "Re-analyse")),
        ),
    },
    {
        "id": "k3", "title": "Paper Walls", "artist": "Hallow Youth", "state": "catalogue",
        "bpm": "128", "key": "A min", "analysed": "yes", "campaigns": "0",
        "streams": "2.1k", "spark": "▂▁▁▁▁▁▁",
        "insp": (
            Section("Track", "kv", (("title", "Paper Walls"), ("window", "catalogue"))),
            Section("", "actions", ("Start campaign", "Re-analyse")),
        ),
    },
    {
        "id": "k4", "title": "Untitled demo", "artist": "Just One Branch", "state": "unanalysed",
        "bpm": "—", "key": "—", "analysed": "no", "campaigns": "0",
        "streams": "—", "spark": "▁▁▁▁▁▁▁",
        "insp": (
            Section("Track", "kv", (("title", "Untitled demo"), ("analysed", "no"))),
            Section("Note", "note", (
                "Nothing downstream can run until it is analysed once. Every outreach process "
                "is a query against those derived facts, never a re-analysis.",
            )),
            Section("", "actions", ("Analyse now", "Upload master")),
        ),
    },
)

TRACKS = View(
    key="tracks", title="Tracks",
    blurb="Analysed once. Every campaign, pitch and asset is a query against these "
          "facts rather than a re-analysis.",
    stats=(("tracks", "4", ""), ("analysed", "3", _bar(75)),
           ("in launch", "1", ""), ("campaigns live", "3", ""),
           ("streams / 30d", "51.7k", "▂▃▃▄▆▇█")),
    cols=(Col("title", "Track", "b", "20%"), Col("artist", "Artist", "", "16%"),
          Col("state", "Window", "chip", "12%"), Col("bpm", "BPM", "num", "7%"),
          Col("key", "Key", "mono", "9%"), Col("campaigns", "Camp", "num", "6%"),
          Col("streams", "30d", "num", "10%"), Col("spark", "", "spark", "10%")),
    rows=_TRACK_ROWS,
)


# ----------------------------------------------------------------- campaigns

_CAMPAIGN_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": "m1", "name": "Cold Open · UGC", "artist": "Hallow Youth", "channel": "ugc",
        "state": "running", "bar": _bar(62), "short": "100", "sent": "85",
        "replied": "21", "agreed": "9", "delivered": "7",
        "insp": (
            Section("Campaign", "kv", (
                ("track", "Cold Open"), ("channel", "ugc"), ("goal", "40 posts"),
                ("state", "running · day 14"), ("shortlisted", "100"),
                ("contacted", "85"), ("holdout", "15"),
            )),
            Section("Ladder", "kv", (
                ("rung 1 · replied", "21 of 85 · 25%"),
                ("rung 2 · delivered", "7 verified"),
                ("rung 3 · local effect", "media 214k views"),
                ("rung 4 · streams", "41.2k /30d — reported, not attributed"),
            )),
            Section("Holdout", "note", (
                "15 matched counterparties deliberately not contacted. Without them, “streams "
                "rose during the campaign” is a sentence, not a finding.",
            )),
            Section("", "actions", ("Open threads", "Pause", "Extend shortlist", "Export")),
        ),
    },
    {
        "id": "m2", "name": "Cold Open · Radio", "artist": "Hallow Youth", "channel": "radio",
        "state": "running", "bar": _bar(30), "short": "40", "sent": "34",
        "replied": "5", "agreed": "1", "delivered": "0",
        "insp": (
            Section("Campaign", "kv", (
                ("track", "Cold Open"), ("channel", "radio"),
                ("state", "running"), ("playbook", "radio · from channel_playbook"),
            )),
            Section("Note", "note", (
                "Second channel, zero new agents. A channel is a playbook row plus a contact "
                "adapter — the spine, the fleet and the coordination are untouched.",
            )),
            Section("", "actions", ("Open threads", "Pause", "Export")),
        ),
    },
    {
        "id": "m3", "name": "Slow Exit · Press", "artist": "Amanda Kurt", "channel": "press",
        "state": "winding down", "bar": _bar(80), "short": "31", "sent": "31",
        "replied": "3", "agreed": "1", "delivered": "1",
        "insp": (
            Section("Campaign", "kv", (
                ("track", "Slow Exit"), ("channel", "press"), ("state", "winding down"),
                ("reply rate", "10% · below UGC"),
            )),
            Section("Lesson written", "quote", (
                "“Press replies are slower and rarer than UGC for this artist; lead with the "
                "live reputation rather than the release.” — scope: artist, confidence 0.5",
            )),
            Section("", "actions", ("Open threads", "Close", "Export")),
        ),
    },
)

CAMPAIGNS = View(
    key="campaigns", title="Campaigns",
    blurb="One track, one channel, one goal. Channels run in parallel because a channel "
          "is data, not code.",
    stats=(("running", "2", ""), ("contacted", "150", _bar(72)),
           ("reply rate", "19%", _bar(19)), ("delivered", "8", ""),
           ("holdout", "15", "")),
    cols=(Col("name", "Campaign", "b", "22%"), Col("artist", "Artist", "", "14%"),
          Col("channel", "Channel", "chip", "9%"), Col("state", "State", "chip", "13%"),
          Col("bar", "", "bar", "10%"), Col("sent", "Sent", "num", "7%"),
          Col("replied", "Repl", "num", "7%"), Col("agreed", "Agr", "num", "6%"),
          Col("delivered", "Deliv", "num", "7%")),
    rows=_CAMPAIGN_ROWS,
)


# ------------------------------------------------------------------- artists

def artist_view(rows: list[dict[str, Any]]) -> View:
    """The one view backed by the real database.

    Live rows, wireframe columns: `tracks`, `facts` and the rest are fixtures because
    those tables do not exist. Mixing them is deliberate — it shows exactly where the
    real substrate currently stops.
    """
    demo_extra = {
        "hallow-youth":    ("4", "312", "2", "41.2k", "▁▂▂▃▅▆█", "94%", _bar(94)),
        "amanda-kurt":     ("2", "128", "1", "8.4k",  "▃▃▂▂▂▁▁", "21%", _bar(21)),
        "just-one-branch": ("1", "0",   "0", "—",     "▁▁▁▁▁▁▁", "0%",  _bar(0)),
    }
    out: list[dict[str, Any]] = []
    for r in rows:
        tracks, facts, camps, streams, spark, pct, bar = demo_extra.get(
            r["slug"], ("0", "0", "0", "—", "▁▁▁▁▁▁▁", "0%", _bar(0)))
        out.append({
            "id": str(r["id"]), "name": r["name"], "type": r["type"],
            "tracks": tracks, "facts": facts, "camps": camps,
            "streams": streams, "spark": spark, "budget": pct, "bar": bar,
            "insp": (
                Section("Artist", "kv", (
                    ("name", r["name"]), ("type", r["type"]),
                    ("slug", r["slug"]), ("status", r["status"]),
                    ("added", r["created_at"].strftime("%Y-%m-%d")),
                    ("source", "live · artist table"),
                )),
                Section("Platforms", "kv", (
                    ("spotify", "owned · connected"),
                    ("instagram", "owned · standard access"),
                    ("youtube", "unowned · searched"),
                    ("tiktok", "absent · searched anyway"),
                    ("open web", "searched"),
                )),
                Section("Note", "note", (
                    "An artist with no account on a platform is still worth searching there — "
                    "fan activity is content about them whether they take part or not. Mode is "
                    "per artist per platform, which is what makes a one-account act and a "
                    "five-account act the same code path.",
                )),
                Section("", "actions", ("Open facts", "Edit", "Run forage", "Budgets")),
            ),
        })
    return View(
        key="artists", title="Artists",
        blurb="The spine. Relationships, audience model and lessons accumulate here and "
              "are inherited by every release.",
        stats=(("roster", str(len(out)), ""), ("live rows", "yes", ""),
               ("tracks", "4", ""), ("facts", "440", ""), ("campaigns", "3", "")),
        cols=(Col("name", "Artist", "b", "22%"), Col("type", "Type", "chip", "12%"),
              Col("tracks", "Tracks", "num", "8%"), Col("facts", "Facts", "num", "8%"),
              Col("camps", "Camp", "num", "7%"), Col("streams", "30d", "num", "10%"),
              Col("spark", "", "spark", "10%"), Col("budget", "Budget", "num", "8%"),
              Col("bar", "", "bar", "12%")),
        rows=tuple(out),
        empty="No artists yet — add one from the roster form.",
    )


# --------------------------------------------------------------------- today

#: The home screen. A queue, not a dashboard: if it is empty the fleet is healthy and
#: you close the tab. The operator's attention is the scarce resource at a one-person
#: label, so the front page spends it rather than decorating it.
TODAY = (
    {
        "id": "n1", "sev": "act", "icon": "▲", "kind": "Approve",
        "head": "3 drafts ready to send",
        "sub": "@example_creator and 2 more · UGC · Hallow Youth · Cold Open",
        "cta": "Review", "href": "/approvals",
        "insp": (
            Section("Why this is here", "note", (
                "Sending is the only irreversible act in the system, so no draft leaves "
                "without a human. The drafter has written them and set each thread to "
                "awaiting_human; nothing moves until you say so.",
            )),
            Section("Drafts", "kv", (
                ("@example_creator", "UGC · rate proposed"),
                ("@example_two", "UGC · no rate"),
                ("@example_three", "UGC · follow-up"),
            )),
            Section("", "actions", ("Review all", "Approve all", "Snooze")),
        ),
    },
    {
        "id": "n2", "sev": "act", "icon": "✉", "kind": "Decide",
        "head": "Example Radio Nova asked for a rate",
        "sub": "replied 4h ago · negotiating · the negotiator is off",
        "cta": "Open", "href": "/threads",
        "insp": (
            Section("Thread", "kv", (
                ("counterparty", "Example Radio Nova"), ("state", "negotiating"),
                ("waiting", "4h"), ("agent", "negotiator · disabled"),
            )),
            Section("Note", "note", (
                "It will sit here indefinitely rather than guess a number. That is the correct "
                "behaviour for a deferred agent, not a stall.",
            )),
            Section("", "actions", ("Draft reply", "Open thread", "Enable negotiator")),
        ),
    },
    {
        "id": "n3", "sev": "warn", "icon": "⚡", "kind": "Conflict",
        "head": "rights.master disagrees with itself",
        "sub": "you said Self-owned · a registry crawl says licensed",
        "cta": "Resolve", "href": "/facts",
        "insp": (
            Section("Contradiction", "kv", (
                ("dimension", "rights.master"), ("asserted", "Self-owned · you · 2026-07-30"),
                ("measured", "licensed to a label · registry · 2026-08-06"),
                ("state", "open"),
            )),
            Section("Why nothing was decided", "note", (
                "An inferred value may never overwrite a measured one, and a human assertion is "
                "a third thing again. Rights are exactly where a silent auto-resolve costs real "
                "money, so the system refuses and asks.",
            )),
            Section("", "actions", ("Keep mine", "Accept crawl", "Both wrong", "Open sources")),
        ),
    },
    {
        "id": "n4", "sev": "warn", "icon": "◑", "kind": "Budget",
        "head": "Hallow Youth at 94% of today's cap",
        "sub": "18.2k of 20k tokens · 37 leads already refused · resets 00:00",
        "cta": "Raise", "href": "/budgets",
        "insp": (
            Section("Budget", "kv", (
                ("scope", "artist · Hallow Youth"), ("tokens", "18,204 of 20,000"),
                ("leads refused", "37"), ("resets", "00:00 UTC"),
            )),
            Section("Note", "note", (
                "The 37 refusals are recorded, not silent. Silent truncation reads as “we "
                "covered everything” when it did not.",
            )),
            Section("", "actions", ("Raise cap", "View dropped", "Pause artist")),
        ),
    },
    {
        "id": "n5", "sev": "info", "icon": "●", "kind": "Error",
        "head": "web_search adapter rate limited ×3",
        "sub": "4 leads waiting · retrying in 8m · no facts affected",
        "cta": "Inspect", "href": "/runs",
        "insp": (
            Section("Failure", "kv", (
                ("adapter", "web_search"), ("http", "429"), ("occurrences", "3 in 1h"),
                ("leads waiting", "4"), ("facts affected", "0"),
                ("next retry", "8m"),
            )),
            Section("Note", "note", (
                "Self-healing by backoff — here because you should know an adapter is "
                "degraded, not because you need to do anything.",
            )),
            Section("", "actions", ("Retry now", "Silence 1h", "Disable adapter")),
        ),
    },
)

#: The reassurance line under the queue. Reads as status, not as a metric to optimise.
TODAY_QUIET = (
    ("fleet", "healthy"), ("agents", "10 · 4 working"), ("errors 1h", "4 · 0 fatal"),
    ("last run", "40s ago"), ("db idle cost", "$0.00"),
)


# ----------------------------------------------------------------- approvals

APPROVALS = (
    {
        "id": "p1", "who": "@example_creator", "channel": "ugc", "artist": "Hallow Youth",
        "track": "Cold Open", "waiting": "2h",
        "subject": "Cold Open — 21k median views, would this fit your feed?",
        "body": (
            "Hi — I run a small label in Berlin. We put out Cold Open by Hallow Youth two "
            "weeks ago and it has been finding people who like exactly the kind of thing you "
            "post.\n\n"
            "The hook lands at 0:47 and runs seventeen seconds, which is the part that seems "
            "to travel.\n\n"
            "Happy to send the audio and a rate. No obligation either way, and I won't chase "
            "you if it isn't a fit."
        ),
        "insp": (
            Section("Draft", "kv", (
                ("thread", "#88"), ("state", "awaiting_human"),
                ("drafted", "2h ago by drafter"), ("model", "claude-sonnet-5"),
                ("tokens", "1,842"), ("channel", "ugc"),
            )),
            Section("What it drew on", "chain", (
                (0, "fact", "sound.hook = 0:47–1:04 · measured", "party_fact"),
                (0, "fact", "audience.city = Berlin · measured", "party_fact"),
                (0, "metric", "median views 21,400 · measured", "party_metric"),
                (0, "lesson", "“open with the live reputation”", "lesson"),
            )),
            Section("Checks", "kv", (
                ("disclosure", "required if paid · not yet added"),
                ("suppression", "clear"), ("open thread elsewhere", "none"),
                ("prior contact", "none"),
            )),
            Section("", "actions", ("Approve & send", "Edit", "Reject", "Reject & teach")),
        ),
    },
    {
        "id": "p2", "who": "@example_two", "channel": "ugc", "artist": "Hallow Youth",
        "track": "Cold Open", "waiting": "2h",
        "subject": "Cold Open — thought of your last three posts",
        "body": (
            "Hi — quick one. Cold Open by Hallow Youth, out two weeks, 142bpm and about as "
            "cold as the title suggests.\n\n"
            "No rate attached yet; tell me what you normally charge and I'll say yes or no "
            "honestly."
        ),
        "insp": (
            Section("Draft", "kv", (
                ("thread", "#91"), ("state", "awaiting_human"), ("drafted", "2h ago"),
            )),
            Section("Checks", "kv", (
                ("disclosure", "n/a · no payment offered"), ("suppression", "clear"),
            )),
            Section("", "actions", ("Approve & send", "Edit", "Reject", "Reject & teach")),
        ),
    },
    {
        "id": "p3", "who": "@example_three", "channel": "ugc", "artist": "Hallow Youth",
        "track": "Cold Open", "waiting": "5h",
        "subject": "following up on Cold Open",
        "body": (
            "Following up once and then I'll leave it — did Cold Open land for you?\n\n"
            "If it's a no, a one-word reply is genuinely fine and I'll take you off the list."
        ),
        "insp": (
            Section("Draft", "kv", (
                ("thread", "#77"), ("state", "awaiting_human"),
                ("follow-up", "1 of max 1"),
            )),
            Section("Note", "note", (
                "Follow-up cadence is capped by the playbook, so the fleet cannot decide on its "
                "own to chase somebody a fourth time.",
            )),
            Section("", "actions", ("Approve & send", "Edit", "Reject", "Close as no-reply")),
        ),
    },
)


# --------------------------------------------------------------------- inbox

INBOX = (
    {
        "id": "i1", "who": "Example Radio Nova", "artist": "Hallow Youth", "when": "4h ago",
        "intent": "asked a question", "state": "needs you",
        "preview": "Thanks — what's the rate for a four-week rotation?",
        "insp": (
            Section("Message", "kv", (
                ("from", "Example Radio Nova"), ("channel", "email"),
                ("received", "4h ago"), ("classified", "question · rate"),
                ("confidence", "0.88"), ("thread state", "→ negotiating"),
            )),
            Section("Full reply", "quote", (
                "Thanks — what's the rate for a four-week rotation? We'd want it for the "
                "evening slot, and we'd need the clean edit.",
            )),
            Section("Note", "note", (
                "The inbox classified it and advanced the thread, then stopped. Rung 1 of the "
                "ladder: fast, clean, and the signal the scorer actually learns from.",
            )),
            Section("", "actions", ("Draft reply", "Open thread", "Mark handled")),
        ),
    },
    {
        "id": "i2", "who": "@example_creator", "artist": "Hallow Youth", "when": "1d ago",
        "intent": "agreed", "state": "handled",
        "preview": "works for me — send the audio",
        "insp": (
            Section("Message", "kv", (
                ("classified", "agreement"), ("confidence", "0.94"),
                ("thread state", "→ agreed"), ("next", "assets delivered"),
            )),
            Section("", "actions", ("Open thread",)),
        ),
    },
    {
        "id": "i3", "who": "example-blog.test", "artist": "Amanda Kurt", "when": "2d ago",
        "intent": "declined", "state": "handled",
        "preview": "not for us this time, but keep sending",
        "insp": (
            Section("Message", "kv", (
                ("classified", "soft decline"), ("confidence", "0.79"),
                ("thread state", "→ closed_lost"), ("relationship", "kept warm"),
            )),
            Section("Lesson written", "quote", (
                "“This outlet declines release pitches but invites future contact — lead with "
                "the artist story, not the release.”",
            )),
            Section("", "actions", ("Open thread", "View lesson")),
        ),
    },
    {
        "id": "i4", "who": "@example_dnc", "artist": "Hallow Youth", "when": "3w ago",
        "intent": "unsubscribed", "state": "suppressed",
        "preview": "please don't contact me again",
        "insp": (
            Section("Message", "kv", (
                ("classified", "unsubscribe"), ("confidence", "0.99"),
                ("action taken", "suppressed permanently"),
            )),
            Section("Note", "note", (
                "Suppression is permanent and survives every future campaign, artist and sweep. "
                "There is no path in the product that reopens it.",
            )),
            Section("", "actions", ("View history",)),
        ),
    },
)


VIEWS: dict[str, View] = {
    v.key: v for v in (FACTS, QUEUE, FLEET, BUDGETS, RUNS, COUNTERPARTIES,
                       THREADS, TRACKS, CAMPAIGNS)
}


def select(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
           sel: str | None) -> dict[str, Any] | None:
    """The row the inspector is showing. Defaults to the first, so the third pane is
    never empty on arrival — an empty inspector teaches nothing about the layout."""
    if not rows:
        return None
    for row in rows:
        if row["id"] == sel:
            return row
    return rows[0]
