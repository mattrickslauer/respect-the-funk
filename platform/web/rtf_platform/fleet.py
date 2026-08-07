"""The fleet and the substrate, as data.

Every agent in `docs/superpowers/specs/2026-08-07-agent-contract-design.md` is a row in
`agent_manifest` plus a handler function. That table does not exist yet, and building it
before there is a runtime to read it would be a table with no reader.

So the manifest lives here first, as a type — the same move `domain.ArtistType` makes and
for the same reason: one definition that the page, any validation and anything downstream
that branches on agent kind all read from. When `agent_manifest` becomes a table, this
module becomes the migration's seed and the `Agent` fields become its columns, one for one.
That is deliberate. A prototype whose shape is not the eventual shape teaches nothing.

**Nothing here is invented.** Every row cites the section it comes from, and `state` says
plainly what is running versus what is written down. The console renders that distinction
rather than smoothing it over, because a roadmap that looks like a status page is worse
than no page at all — the house rule from `infra/MEMORY-WORKLOAD.md` and
`content/bin/screen_clips.py`, applied to our own progress.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    """What is actually true of a piece right now.

    Three values rather than a boolean, because "designed and next" and "designed and
    deferred" are different answers to *should I be looking at this*, and collapsing them
    makes the page lie in the flattering direction.
    """

    def __new__(cls, value: str, label: str, blurb: str) -> "State":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label      # type: ignore[attr-defined]
        obj.blurb = blurb      # type: ignore[attr-defined]
        return obj

    LIVE    = ("live",    "Live",    "Running against the real cluster.")
    NEXT    = ("next",    "Next",    "Specified, in the build, not written yet.")
    PLANNED = ("planned", "Planned", "Specified and deliberately deferred.")


class Scope(str, Enum):
    """Which tier of work an agent claims — the `scope_kind` column from §7."""

    TENANT = "tenant"    # label-wide: the counterparty index has no artist
    ARTIST = "artist"    # one act, across releases
    TRACK  = "track"     # one release, where the effort goes


# --------------------------------------------------------------- the contract

@dataclass(frozen=True)
class Layer:
    name: str
    network: bool
    database: bool
    decisions: bool
    note: str


#: §2. Three layers, each pure with respect to a different thing. This table is the
#: whole modularity claim — an adapter is testable with no network, a handler with no
#: database, and the runtime is written once for every agent in the fleet.
LAYERS: tuple[Layer, ...] = (
    Layer("Adapter", True,  False, False,
          "fetch(input, credentials) -> result. Testable against recorded fixtures, "
          "offline."),
    Layer("Handler", False, False, True,
          "handle(work, ctx) -> Effects. Returns what it wants to happen. Testable with "
          "no database and no network."),
    Layer("Runtime", False, True,  False,
          "Claims, leases, applies Effects in one transaction, enforces the blast radius. "
          "Written once."),
)


# ------------------------------------------------------------------ the fleet

@dataclass(frozen=True)
class Agent:
    """One row of `agent_manifest`, plus the prose a table cannot hold."""

    kind: str
    title: str
    purpose: str
    work_table: str
    scopes: tuple[Scope, ...]
    adapters: tuple[str, ...]
    writes: tuple[str, ...]
    woken_by: str
    state: State
    source: str

    @property
    def scope_labels(self) -> str:
        return ", ".join(s.value for s in self.scopes)


#: `PLATFORM-SPEC §5` named eight. The contract design adds three — Forager, Distiller,
#: Invalidator — and drops RemixKit, which §5 already says is a tool the Drafter calls
#: rather than a peer. Order is the build order from the contract design §12c.
AGENTS: tuple[Agent, ...] = (
    Agent(
        "forager", "Forager",
        "Builds the internet's picture of our own artists, and verifies that outreach "
        "actually landed.",
        "lead", (Scope.ARTIST, Scope.TRACK),
        ("spotify", "musicbrainz", "web_search", "web_page", "instagram_owned"),
        ("artist_fact", "artist_metric", "artist_document"),
        "its own cadence, and any new lead",
        State.NEXT, "Forager design",
    ),
    Agent(
        "distiller", "Distiller",
        "Turns fetched prose into claims that carry provenance and cite what they stand on.",
        "chunk_staging", (Scope.ARTIST, Scope.TRACK),
        (),
        ("artist_fact",),
        "an artist_chunk insert",
        State.NEXT, "Forager design §6",
    ),
    Agent(
        "invalidator", "Invalidator",
        "Walks the basis graph down from a retracted fact and marks everything standing "
        "on it stale. Never deletes.",
        "fact_basis", (Scope.TENANT, Scope.ARTIST, Scope.TRACK),
        (),
        ("artist_fact", "counterparty_observation", "artist_audience", "lesson"),
        "any fact whose status flips",
        State.NEXT, "contract §5b",
    ),
    Agent(
        "scout", "Scout",
        "Finds people who can get the music out — creators, playlist runners, blogs, "
        "programmers — and records how we found each one.",
        "lead", (Scope.TENANT,),
        ("web_search", "spotify", "youtube", "manual"),
        ("counterparty", "counterparty_contact"),
        "a label-wide sweep cadence",
        State.NEXT, "contract §10",
    ),
    Agent(
        "drafter", "Drafter",
        "Writes the outreach, using what the fleet already knows about the act, the "
        "track and the person.",
        "thread", (Scope.TRACK,),
        (),
        ("message", "thread"),
        "thread.state -> approved",
        State.NEXT, "PLATFORM-SPEC §5",
    ),
    Agent(
        "sender", "Sender",
        "Performs the one irreversible act. Claims from the outbox so a crash retries "
        "against the same idempotency key.",
        "outbox", (Scope.TRACK,),
        ("email",),
        (),
        "an outbox insert",
        State.NEXT, "PLATFORM-SPEC §3b",
    ),
    Agent(
        "inbox", "Inbox",
        "Classifies replies and decides the next state. Rung 1 of the feedback ladder "
        "starts here.",
        "message", (Scope.TRACK,),
        (),
        ("thread", "lesson"),
        "an inbound message insert",
        State.NEXT, "PLATFORM-SPEC §5",
    ),
    Agent(
        "researcher", "Researcher",
        "Enriches one counterparty before we approach them.",
        "thread", (Scope.TENANT,),
        ("web_search", "web_page"),
        ("counterparty_observation",),
        "thread.state -> shortlisted",
        State.PLANNED, "PLATFORM-SPEC §5",
    ),
    Agent(
        "negotiator", "Negotiator",
        "Drives rate and terms to agreement.",
        "thread", (Scope.TRACK,),
        (),
        ("message", "thread"),
        "thread.state -> negotiating",
        State.PLANNED, "PLATFORM-SPEC §5",
    ),
    Agent(
        "analyst", "Analyst",
        "Updates the audience model and kills hypotheses the results have not supported.",
        "thread", (Scope.ARTIST,),
        (),
        ("artist_audience", "lesson"),
        "a counterparty_observation insert",
        State.PLANNED, "PLATFORM-SPEC §5",
    ),
)


# --------------------------------------------------------------- the substrate

@dataclass(frozen=True)
class Table:
    name: str
    area: str
    purpose: str
    state: State


#: Every table the design calls for, and what is actually in `defaultdb`. Two of thirty
#: is the honest number today and the page says so — `platform/README.md`'s rule that a
#: table arrives when something needs it, made visible rather than asserted.
SUBSTRATE: tuple[Table, ...] = (
    Table("tenant",  "Roots", "The label.", State.LIVE),
    Table("artist",  "Roots", "The spine. Where knowledge compounds across releases.", State.LIVE),
    Table("track",   "Roots", "Where the fleet spends its effort.", State.NEXT),

    Table("track_measurement", "Derived — measured",
          "BPM, key, drop, hook window. No confidence column, because a measurement has none.",
          State.NEXT),
    Table("track_character", "Derived — inferred",
          "Mood, era, reference artists, embedding. Versioned by supersedes_id, never "
          "overwritten.", State.NEXT),
    Table("track_rights", "Derived — asserted",
          "Splits, clearance. A human said it and is accountable for it.", State.PLANNED),
    Table("artist_audience", "Derived — inferred",
          "The audience model. Inherited by every track by default.", State.PLANNED),

    Table("artist_profile", "Research",
          "One row per artist per platform: owned, unowned, or absent.", State.NEXT),
    Table("lead", "Research",
          "The frontier. One row per thing worth fetching, scored, leased, deduped.",
          State.NEXT),
    Table("artist_document", "Research", "What was fetched, hashed so it is fetched once.",
          State.NEXT),
    Table("artist_chunk", "Research", "Embedded passages. Searchable at commit.", State.NEXT),
    Table("artist_fact", "Research",
          "A claim with provenance, status and a chain of what superseded what.", State.NEXT),
    Table("artist_metric", "Research",
          "Append-only time series. Streams, views, followers. Rung 4 lands here.",
          State.NEXT),
    Table("artist_identifier", "Research",
          "Canonical IDs — ISRC, MBID, Spotify. The anchor that stops name drift.",
          State.NEXT),
    Table("artist_budget", "Research",
          "The limits. Read on the hot path, never written to it.", State.NEXT),
    Table("dimension_policy", "Research",
          "How long each kind of claim stays fresh before it needs rechecking.",
          State.PLANNED),
    Table("chunk_staging", "Research",
          "A hedge against vector-index write degradation. Delete it if measurement "
          "clears the risk.", State.PLANNED),
    Table("verdict", "Research", "A human said relevant or not. Trains the scorer.",
          State.PLANNED),
    Table("contradiction", "Research",
          "Where asserted and measured disagree and the system refuses to pick.",
          State.PLANNED),

    Table("fact_basis", "The basis graph",
          "What every belief stands on. Walks up for provenance, down for invalidation "
          "and credit.", State.NEXT),

    Table("counterparty", "Counterparty",
          "Creators, programmers, curators, press, sync — one shape, many kinds. A label "
          "asset, not an artist's.", State.NEXT),
    Table("counterparty_contact", "Counterparty",
          "How to reach them, where we got it, and whether they have asked us not to.",
          State.NEXT),
    Table("counterparty_observation", "Counterparty",
          "Append-only. An estimate may never overwrite a measurement.", State.PLANNED),

    Table("campaign", "Outreach", "One track, one channel, one goal.", State.NEXT),
    Table("thread", "Outreach",
          "One conversation. The state machine and the lease live in the same row.",
          State.NEXT),
    Table("message", "Outreach",
          "Every word in and out. If it is not a row here, it did not happen.", State.NEXT),
    Table("outbox", "Outreach",
          "Written in the same transaction as the message, so a crash cannot double-send.",
          State.NEXT),
    Table("channel_playbook", "Outreach",
          "A channel is data, not code. Adding press is a row.", State.PLANNED),

    Table("lesson", "Memory & fleet",
          "What we learned, scoped to an artist, a kind, a channel, or everything.",
          State.PLANNED),
    Table("agent_run", "Memory & fleet",
          "Not telemetry. What makes a fleet restartable and a decision explainable — "
          "and where spend is computed from, so no counter row goes hot.", State.NEXT),
    Table("agent_manifest", "Memory & fleet",
          "This module, once there is a runtime to read it.", State.NEXT),
)


#: Rendering order. Dict ordering is insertion order, and grouping in the template would
#: put the areas in whatever order the tuple happens to hit them.
AREAS: tuple[str, ...] = (
    "Roots", "Derived — measured", "Derived — inferred", "Derived — asserted",
    "Research", "The basis graph", "Counterparty", "Outreach", "Memory & fleet",
)


def substrate_by_area() -> list[tuple[str, list[Table]]]:
    return [(area, [t for t in SUBSTRATE if t.area == area]) for area in AREAS]


def counts() -> dict[str, int]:
    return {s.value: sum(1 for t in SUBSTRATE if t.state is s) for s in State}


# ---------------------------------------------------------- the feedback ladder

@dataclass(frozen=True)
class Rung:
    n: int
    signal: str
    latency: str
    attributable: str
    used_for: str


#: §9. The system sends emails and hopes the track performs. Streams are the goal and the
#: worst possible trainer — confounded by playlist adds, algorithmic push, seasonality and
#: our own channels firing at once. So the fast rungs train the loop and the slow one only
#: audits it.
LADDER: tuple[Rung, ...] = (
    Rung(1, "They replied — yes, no, and why", "hours–days", "Yes, cleanly",
         "Training: pitch angle, targeting, playbook"),
    Rung(2, "They actually posted, spun, published, added", "days–weeks", "Yes, verifiable",
         "Training: was the yes real"),
    Rung(3, "Their post did 40k; market Shazams moved", "weeks", "Partially",
         "Training, discounted"),
    Rung(4, "The track's stream count", "weeks–months", "No — confounded",
         "Reporting only. Never trains the scorer."),
)


# ------------------------------------------------------------------- platforms

@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    owned_api: str
    absent_note: str


#: §3 of the Forager design: every artist is a different shape, and an artist with no
#: account on a platform is still worth searching there — fan activity is content about
#: them whether they participate or not. `mode` is per artist per platform, so this list
#: is the axis the research page renders against.
PLATFORMS: tuple[Platform, ...] = (
    Platform("spotify", "Spotify", "Web API — catalogue, IDs, related artists",
             "Playlist placements by other people still count."),
    Platform("instagram", "Instagram", "Graph API, Standard Access for owned accounts",
             "Tags and mentions exist without an account."),
    Platform("youtube", "YouTube", "Data API — videos, views, comments",
             "Covers, reactions and uploads by fans."),
    Platform("tiktok", "TikTok", "Read-only; no compliant sound-page API",
             "Sound usage is the highest-signal source and is browsed manually."),
    Platform("musicbrainz", "MusicBrainz", "Open API — canonical identity",
             "Releases are catalogued whether or not the act participates."),
    Platform("web", "Open web & press", "Search API, then page fetch",
             "Reviews and mentions are the point; nobody owns an account here."),
)
