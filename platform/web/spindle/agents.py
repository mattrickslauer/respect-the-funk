"""Concrete agents. Each one is a function that takes a claimed lead and writes rows.

`fleet.py` is the coordination primitive; this is what gets coordinated. The contract is
deliberately small — `(conn, lead, gate) -> Outcome` — because an agent that needs more
than a row and a budget is an agent that has grown a dependency on something other than
memory, which is the thing `PLATFORM-SPEC §0` says must not happen.

No agent in this module knows another one exists. `Outcome.follow_on` writes a lead; a
lead is claimed by whoever handles that kind. That indirection is not ceremony — it is
what makes the fleet restartable, because the handoff survives both agents dying.

## What is embedded, and why it is this corpus

The roster is three unsigned artists with no MusicBrainz presence — checked, not assumed.
There is therefore no public evidence corpus to index for them, and inventing one would
break the house rule that `platform/README.md` states plainly: nothing in this system is
a fabricated person, outlet or quote.

What the label does own is `docs/research/` — thirteen reports plus a synthesis, written
over the research phase, about how this industry actually behaves. `PLATFORM-SPEC §6`
calls R2 *"what have we learned that applies here?"* and that corpus is the literal
answer. Embedding it makes retrieval demonstrable over text that is real, ours, and
already the thing the architecture said memory was for.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import psycopg

from psycopg.types.json import Json

from spindle import (
    contacts, embed, fcc, fleet, harvested, lessons, podcastindex, radiobrowser, repo,
    settings as settings_mod, sources, spend, streams, wikipedia,
)

#: Target chunk size in characters. ~400 tokens at four characters per token, which is
#: large enough to carry an argument and small enough that a hit points somewhere
#: specific. A chunk the size of a document retrieves everything and locates nothing.
CHUNK_CHARS = 1600

#: Overlap between adjacent chunks, so a claim split across a boundary is still findable
#: whole from one side of it.
OVERLAP_CHARS = 200

#: How many candidates R1 fetches before the rerank sees them. Wider than the caller's
#: `limit`, because a rerank that can only reorder the rows it is going to return cannot
#: promote anybody into them — and promoting a good match that similarity alone ranked
#: 24th is the entire point of reading the lessons.
SHORTLIST_CANDIDATES = 50


def content_hash(text: str) -> str:
    """Stable identity for a document, used for `UNIQUE (tenant_id, content_hash)`.

    Hashing the body rather than the URL means re-ingesting an unchanged document is a
    no-op and an edited one is a new row, which is the behaviour the append-only
    evidence model wants.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def split(text: str, *, size: int = CHUNK_CHARS,
          overlap: int = OVERLAP_CHARS) -> list[str]:
    """Break text into overlapping chunks on paragraph boundaries where possible.

    Splitting mid-sentence is the cheap way and it costs retrieval quality: an embedding
    of half a claim sits in a different place than the claim. So paragraphs are packed
    until they exceed the budget, and only a single paragraph longer than the budget is
    split by force.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), size - overlap):
                chunks.append(para[i:i + size])
            continue
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            # Carry the tail of the last chunk so a boundary-straddling claim survives.
            current = (current[-overlap:] + "\n\n" + para) if overlap else para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current:
        chunks.append(current)
    return chunks


def _fetch_embed_document(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate) -> dict[str, Any]:
    """Load the document, chunk it, and embed the chunks. No writes — `embed_batch` calls
    an embedding provider over the network, so nothing here may run inside a transaction.
    """
    document_id = lead["target"]
    tenant_id = lead["tenant_id"]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, tenant_id, party_id, title, body FROM party_document
                WHERE tenant_id = %s AND id = %s""",
            (tenant_id, document_id),
        )
        doc = cur.fetchone()

    if doc is None:
        # The document was deleted between the lead being written and claimed. Retrying
        # cannot fix that, so park it rather than burning four more attempts.
        raise fleet.LeadFailed(f"party_document {document_id} is gone", permanent=True)

    body = (doc["body"] or "").strip()
    if not body:
        raise fleet.LeadFailed("document body is empty", permanent=True)

    chunks = split(body)
    if not chunks:
        return {"doc": doc, "chunks": []}

    provider = embed.load()
    vectors, cost = embed.embed_batch(gate, provider, chunks)
    return {"doc": doc, "chunks": chunks, "vectors": vectors, "cost": cost}


def _write_embed_document(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write `party_chunk` rows from what `_fetch_embed_document` already fetched and
    embedded. Nothing here does network I/O, so `fleet.work_once` runs this inside the
    same transaction as `agent_run` and the lead's completion.

    Chunks are written **one `INSERT` at a time**. Not batched, because the vendor
    documents that large batch inserts degrade a vector index.
    """
    doc = prepared["doc"]
    chunks = prepared["chunks"]
    if not chunks:
        return fleet.Outcome(summary="nothing to embed")
    vectors, cost = prepared["vectors"], prepared["cost"]

    with conn.cursor() as cur:
        # Replace rather than append: re-embedding a document with a different model
        # must not leave the old model's chunks behind, because a retrieval filtered
        # to one model would then silently see a partial document.
        cur.execute("DELETE FROM party_chunk WHERE tenant_id = %s AND document_id = %s",
                    (doc["tenant_id"], doc["id"]))
        for ordinal, (text, vector) in enumerate(zip(chunks, vectors)):
            cur.execute(
                """INSERT INTO party_chunk (tenant_id, party_id, document_id, ordinal,
                                            text, embedding, token_count, model)
                   VALUES (%s, %s, %s, %s, %s, %s::VECTOR(1024), %s, %s)""",
                (doc["tenant_id"], doc["party_id"], doc["id"], ordinal, text,
                 vector.literal(), len(text) // 4, vector.model),
            )

    return fleet.Outcome(
        summary=f"{len(chunks)} chunks from {doc['title'][:60]!r}",
        documents=1,
        calls=len(chunks),
        tokens_in=embed.estimate_tokens(chunks),
        cost_usd=cost,
    )


#: `lead.target` is the document id. The document is expected to already exist — this
#: agent turns evidence into something retrievable, it does not go and find evidence.
#: Keeping fetch and embed in separate agents means a provider outage on one does not
#: strand the other, and the boundary between them is a row. Split into fetch/write
#: because `embed_batch` calls an embedding provider over the network — see
#: `fleet.NetworkAgent`.
embedder = fleet.NetworkAgent(fetch=_fetch_embed_document, write=_write_embed_document)


def retrieve(conn: psycopg.Connection, tenant_id: str, query: str, *,
             gate: spend.Gate, limit: int = 5) -> list[dict[str, Any]]:
    """R2 — what do we already know that applies here?

    Every predicate is equality on a prefix column, which is what makes this use the
    vector index rather than over-fetching and discarding. `docs/reference/COCKROACHDB-AI.md`
    has the constraint; migration `007` has the index; `EXPLAIN` on this exact query
    shows a `vector search` node with `prefix spans`.

    The `model` filter is not optional. Without it a cluster that has seen two embedding
    providers returns a ranking computed across incomparable vector spaces — which looks
    like a working search and is noise.

    Same shape `shortlist()` uses, for the same reason: a `JOIN party_document` attached
    to the vector-searched `party_chunk` gives the planner a second table to reason
    about and it abandons `chunk_semantic` for a full scan — measured, not theoretical;
    `EXPLAIN` on the joined form shows `spans: FULL SCAN` over `party_chunk@party_chunk_pkey`
    with an index recommendation, and the query above was written exactly that way until
    an audit caught it. The CTE runs the vector search alone, with nothing joined to it,
    so no join can steer the planner off the index; `title` and `url` are then two scalar
    subqueries against `party_document`'s primary key, which by construction return at
    most one row each, so they cannot duplicate or drop a chunk. Do not "simplify" this
    back into a join.

    Deliberately **not** given an index hint, unlike `shortlist()` and
    `lessons.retrieve_for()`'s general pass. Both of those carry one because their tables
    (`party`, `lesson`) also carry a *second*, tenant-prefixed secondary index the
    optimizer can decide is cheaper for a tenant with few matching rows — `party`'s
    `party_aliases_of` is what actually did, added by a migration this function's table
    has nothing to do with. `party_chunk` has no such alternative: only its primary key
    (on `id` alone, not tenant-prefixed, so it cannot serve a `tenant_id`-scoped scan at
    all) and `chunk_semantic`. There is no other index for the cost model to ever prefer,
    so `EXPLAIN` — asserted for this query in `test_vector_plans.py` — is the whole
    guarantee, not a supplement to a hint; a hint here would pin against a competitor
    that cannot exist unless a future migration adds a second tenant-prefixed index to
    `party_chunk`, at which point this reasoning, and this decision, need revisiting.
    """
    provider = embed.load()
    vectors, _ = embed.embed_batch(gate, provider, [query])
    with conn.cursor() as cur:
        cur.execute(
            """WITH matched AS (
                    SELECT id, document_id, text, ordinal,
                           embedding <=> %s::VECTOR(1024) AS distance
                      FROM party_chunk
                     WHERE tenant_id = %s AND model = %s
                     ORDER BY embedding <=> %s::VECTOR(1024)
                     LIMIT %s
                )
                SELECT m.text, m.ordinal, m.distance,
                       (SELECT d.title FROM party_document d
                         WHERE d.tenant_id = %s AND d.id = m.document_id) AS title,
                       (SELECT d.url FROM party_document d
                         WHERE d.tenant_id = %s AND d.id = m.document_id) AS url
                  FROM matched m
                 ORDER BY m.distance""",
            (vectors[0].literal(), tenant_id, provider.model, vectors[0].literal(), limit,
             tenant_id, tenant_id),
        )
        return list(cur.fetchall())


def _fetch_map_source(conn: psycopg.Connection, lead: dict[str, Any],
                      gate: spend.Gate) -> dict[str, Any]:
    """Resolve the adapter and fetch the surface. `adapter.map_party` is an HTTP call to
    a third party, so nothing here may run inside a transaction — see `fleet.NetworkAgent`.
    """
    party_id = lead.get("party_id")
    if not party_id:
        raise fleet.LeadFailed("map_source needs a party", permanent=True)

    platform = lead.get("platform") or lead.get("adapter") or ""
    try:
        adapter = sources.enabled_for(conn, platform)
    except sources.SourceUnavailable as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    # Free by the manifest's own accounting — but routed through the gate anyway, so a
    # source that later acquires a price cannot start billing without passing a check.
    gate.check(platform if platform in spend.FREE else f"{platform}:fetch")

    try:
        harvest = adapter.map_party(lead["target"])
    except sources.SourceUnavailable as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    return {"platform": platform, "harvest": harvest}


def _write_map_source(conn: psycopg.Connection, lead: dict[str, Any], gate: spend.Gate,
                      prepared: dict[str, Any]) -> fleet.Outcome:
    """Write what `_fetch_map_source` fetched: identifiers, facts, metrics, catalogue and
    the follow-on leads it implies.

    Three rules it does not get to break:

      * **Provenance is not the adapter's opinion, and it is not this function's
        either.** What the platform measured is `measured`, what it classified is
        `inferred`, what a human typed is `asserted`. `harvested.py` is where that
        is enforced: every raw dict is parsed into a frozen `harvested.*` record
        before it is written, and the parse raises `harvested.HarvestInvalid` rather
        than default a label the adapter did not supply. There is no `.get(...,
        default)` left in this function for a missing provenance, unit or
        release_type to hide behind.
      * **A guess is a suggestion, never a fact.** An adapter that matched by name
        returns `suggestions`, and they land in `suggestion` as `pending` for a person
        to accept. Nothing here promotes one.
      * **One transaction.** Facts, metrics, catalogue and the follow-on leads commit
        together with `agent_run` and the lead's own completion — `fleet.work_once`
        wraps all of it, because nothing here does network I/O and holding a
        transaction around a pure database write costs nothing extra.
    """
    platform, harvest = prepared["platform"], prepared["harvest"]
    party_id = lead["party_id"]
    tenant_id = lead["tenant_id"]
    written = {"identifiers": 0, "facts": 0, "metrics": 0,
               "recordings": 0, "releases": 0, "suggestions": 0}

    with conn.cursor() as cur:
        for raw in harvest.identifiers:
            item = harvested.Identifier.parse(raw, adapter=platform)
            cur.execute(
                """INSERT INTO party_identifier
                        (tenant_id, party_id, kind, value, value_raw, provenance,
                         source_lead_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, kind, value) DO NOTHING""",
                (tenant_id, party_id, item.kind, item.value, item.value_raw,
                 item.provenance, lead["id"]),
            )
            written["identifiers"] += cur.rowcount

        for raw in harvest.facts:
            fact = harvested.Fact.parse(raw, adapter=platform)
            # `fact_one_live_per_dimension` is unique per (tenant, party, dimension,
            # provenance) while live, so a second genre would collide with the first.
            # Superseding rather than skipping keeps the newest reading live and the
            # history intact, which is what `supersedes_id` is for — and it has to be
            # *set*, not just implied by `status`, or `lessons.heads()`'s `party_fact`
            # analogue has no edge to resolve a chain from. Found by id first, rather
            # than a blind `UPDATE … WHERE status = 'live'`, so the row the new one
            # names in `supersedes_id` is exactly the row the UPDATE below flips —
            # status and relationship cannot disagree because they come from the same
            # read.
            cur.execute(
                """SELECT id, value_text FROM party_fact
                    WHERE tenant_id = %s AND party_id = %s AND dimension = %s
                      AND provenance = %s AND status = 'live'""",
                (tenant_id, party_id, fact.dimension, fact.provenance),
            )
            live = cur.fetchone()
            if live is not None and live["value_text"] == fact.value_text:
                # Unchanged reading — nothing superseded, nothing new to write.
                continue
            supersedes_id = None
            if live is not None:
                cur.execute(
                    """UPDATE party_fact SET status = 'superseded'
                        WHERE tenant_id = %s AND id = %s AND status = 'live'""",
                    (tenant_id, live["id"]),
                )
                supersedes_id = live["id"]
            cur.execute(
                """INSERT INTO party_fact
                        (tenant_id, party_id, dimension, value_text, provenance,
                         confidence, source, written_by, supersedes_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, fact.dimension, fact.value_text,
                 fact.provenance, fact.confidence, platform, "map_source",
                 supersedes_id),
            )
            written["facts"] += cur.rowcount

        for raw in harvest.metrics:
            metric = harvested.Metric.parse(raw, adapter=platform)
            cur.execute(
                """INSERT INTO party_metric
                        (tenant_id, party_id, platform, entity_kind, metric, value,
                         unit, provenance, source)
                   VALUES (%s, %s, %s, 'party', %s, %s, %s, %s, %s)""",
                (tenant_id, party_id, platform, metric.metric,
                 metric.value, metric.unit, metric.provenance, platform),
            )
            written["metrics"] += 1

        for raw in harvest.recordings:
            track = harvested.Recording.parse(raw, adapter=platform)
            if not track.title:
                continue
            slug = repo.slugify(track.title)[:60] or "untitled"
            if track.isrc:
                slug = f"{slug}-{track.isrc.lower()}"
            cur.execute(
                """INSERT INTO recording (tenant_id, slug, title, isrc, isrc_raw,
                                          duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, slug) DO NOTHING
                RETURNING id""",
                (tenant_id, slug, track.title, track.isrc,
                 raw.get("isrc") or "", track.duration_ms),
            )
            row = cur.fetchone()
            if row is None:
                continue
            written["recordings"] += 1
            # A recording nobody is credited on is orphaned data. The credit is what
            # ties the catalogue back to the artist it was fetched for.
            cur.execute(
                """INSERT INTO party_credit (tenant_id, party_id, subject_kind,
                                             subject_id, role, provenance)
                   VALUES (%s, %s, 'recording', %s, 'main_artist', 'measured')
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, row["id"]),
            )

        for raw in harvest.releases:
            # A blank title is skipped before it ever reaches `Release.parse`, the
            # same order `Recording` already uses — a release the platform
            # returned with no title is junk to drop, not a reason to demand
            # `release_type` from an item that was never going to be written
            # anyway. Checked here rather than in `harvested.py` because whether
            # a blank title is "nothing to store" or "something to reject" is a
            # write-side decision, not the parser's to make.
            if not (raw.get("title") or "").strip():
                continue
            rel = harvested.Release.parse(raw, adapter=platform)
            released_on = rel.release_date[:10] or None

            # A GTIN is the release's real identity and the table has a unique index
            # on it. Without one — Spotify's album payload carries none — fall back
            # to title and date, and accept that a re-release on the same day looks
            # like the same row. The fallback is the weaker test, so it is only used
            # when the strong one is unavailable rather than as well as.
            if rel.gtin:
                cur.execute("SELECT id FROM release WHERE tenant_id = %s AND gtin = %s",
                            (tenant_id, rel.gtin))
            else:
                cur.execute(
                    """SELECT id FROM release
                        WHERE tenant_id = %s AND title = %s
                          AND coalesce(released_on::STRING, '') = coalesce(%s, '')""",
                    (tenant_id, rel.title, released_on),
                )
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO release (tenant_id, title, gtin, gtin_raw,
                                        release_type, released_on)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (tenant_id, rel.title, rel.gtin, raw.get("gtin") or "",
                 rel.release_type,
                 released_on if released_on and len(released_on) == 10 else None),
            )
            written["releases"] += 1

        for hint in harvest.suggestions:
            cur.execute(
                """INSERT INTO suggestion (tenant_id, party_id, kind, payload,
                                           confidence, rationale, source_lead_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (tenant_id, party_id, hint["kind"],
                 psycopg.types.json.Jsonb(hint),
                 hint.get("confidence", 0.5),
                 f"{platform} matched on name — needs a human to confirm",
                 lead["id"]),
            )
            written["suggestions"] += 1

        # The surface has now actually been looked at, which is a different claim
        # from the operator having asserted it exists.
        cur.execute(
            """UPDATE presence SET checked_at = now(), state = 'present'
                WHERE tenant_id = %s AND subject_kind = 'party'
                  AND subject_id = %s AND platform = %s""",
            (tenant_id, party_id, platform),
        )

    return fleet.Outcome(
        summary=harvest.summary or f"mapped {platform}",
        facts=written["facts"],
        metrics=written["metrics"],
        documents=written["recordings"] + written["releases"],
        calls=harvest.calls,
        follow_on=harvest.leads,
    )


#: The agent the product is actually about. An operator asserts "this Spotify page is my
#: artist" and stops there; from that single row this fills in the identifiers, the
#: follower count, the genres, the catalogue and the releases, and writes a lead for
#: every release so the expansion continues without anyone asking. Split into fetch/write
#: because `adapter.map_party` is an HTTP call — see `fleet.NetworkAgent`.
map_source = fleet.NetworkAgent(fetch=_fetch_map_source, write=_write_map_source)


def _fetch_find_counterparties(conn: psycopg.Connection, lead: dict[str, Any],
                               gate: spend.Gate) -> dict[str, Any]:
    """Read what discovery needs and call `adapter.discover_counterparties` — an HTTP
    call, so nothing here may run inside a transaction (see `fleet.NetworkAgent`). The
    reads are ordinary autocommitted statements; they only need to happen before the
    network call, not inside anything.
    """
    party_id = lead.get("party_id")
    if not party_id:
        raise fleet.LeadFailed("find_counterparties needs a party", permanent=True)

    platform = lead.get("platform") or lead.get("adapter") or "deezer"
    try:
        adapter = sources.enabled_for(conn, platform)
    except sources.SourceUnavailable as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc
    if not hasattr(adapter, "discover_counterparties"):
        raise fleet.LeadFailed(f"{platform} cannot discover counterparties",
                               permanent=True)

    gate.check(platform)
    tenant_id = lead["tenant_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM party WHERE tenant_id = %s AND id = %s",
                    (tenant_id, party_id))
        row = cur.fetchone()
        if row is None:
            raise fleet.LeadFailed("the artist is gone", permanent=True)
        artist_name = row["name"]
        cur.execute(
            """SELECT value FROM party_identifier
                WHERE tenant_id = %s AND party_id = %s AND kind = %s""",
            (tenant_id, party_id, f"{platform}_artist"),
        )
        ident = cur.fetchone()
        cur.execute(
            "SELECT max_leads_per_run FROM party_budget WHERE tenant_id = %s AND party_id = %s",
            (tenant_id, party_id),
        )
        budget = cur.fetchone()
        # Style, not name. A curator selects on what a record sounds like, and for an
        # artist nobody has heard of the name returns nothing at all.
        #
        # Two sources, and the second is the one that matters for a new artist. Facts
        # attached to the **party** are what a platform's own genre labels produce —
        # for Hallow Youth that is `Dance` and `Electro`, which is everything Deezer
        # knows and is far too coarse to find a playlist with. Facts attached to a
        # **recording** are what `analyse_recording` writes after listening to the
        # master, and they are specific: `Electronic---Tropical House`.
        #
        # Recording facts carry `recording_id` and a NULL `party_id`, because a genre is
        # a property of the record and not of the person who made it. So they are
        # reached through `party_credit` rather than by party id, and an artist's
        # searchable style is the union over the records they are credited on. Until
        # this join existed, everything the classifier wrote was invisible here and the
        # whole upload-listen-discover chain stopped one step short.
        cur.execute(
            """SELECT DISTINCT value_text FROM party_fact
                WHERE tenant_id = %s AND party_id = %s AND dimension = 'genre'
                  AND status = 'live' LIMIT 5""",
            (tenant_id, party_id),
        )
        terms = [r["value_text"] for r in cur.fetchall()]

        cur.execute(
            """SELECT DISTINCT f.value_text, f.dimension
                 FROM party_fact f
                 JOIN party_credit c ON c.tenant_id = f.tenant_id
                                    AND c.subject_kind = 'recording'
                                    AND c.subject_id = f.recording_id
                WHERE f.tenant_id = %s AND c.party_id = %s AND f.status = 'live'
                  AND f.dimension IN ('style', 'genre', 'style_terms')
                  AND f.recording_id IS NOT NULL
                ORDER BY f.dimension LIMIT 20""",
            (tenant_id, party_id),
        )
        for row in cur.fetchall():
            terms.extend(_search_terms(row["value_text"]))
    cap = int(budget["max_leads_per_run"]) if budget else 25

    try:
        harvest = adapter.discover_counterparties(
            artist_name, ident["value"] if ident else "",
            terms=list(dict.fromkeys(terms))[:8])
    except sources.SourceUnavailable as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    return {"platform": platform, "harvest": harvest, "cap": cap}


def _write_find_counterparties(conn: psycopg.Connection, lead: dict[str, Any],
                               gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the curators `_fetch_find_counterparties` discovered, as parties with
    `party_class = 'counterparty'` — migration 009 argues that at length. They arrive
    `contactable` and unembedded; an `embed_party` lead per curator is what makes them
    findable by R1.

    Nothing here does network I/O, so `fleet.work_once` runs it inside the same
    transaction as `agent_run` and the lead's completion.
    """
    platform, harvest, cap = prepared["platform"], prepared["harvest"], prepared["cap"]
    tenant_id = lead["tenant_id"]

    written = 0
    follow_on: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for cp in harvest.counterparties[:cap]:
            slug = f"{repo.slugify(cp['name'])[:50]}-{platform}-{cp['platform_id']}"
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                      contact_state, status)
                   VALUES (%s, %s, %s, 'person', 'counterparty', 'contactable', 'active')
                   ON CONFLICT (tenant_id, slug) DO NOTHING
                RETURNING id""",
                (tenant_id, slug, cp["name"]),
            )
            created = cur.fetchone()
            if created is None:
                # Already known. Discovery finding the same curator again is the
                # normal case, not an error, and it must not re-queue an embedding.
                continue
            cp_id = created["id"]
            written += 1

            cur.execute(
                """INSERT INTO party_role (tenant_id, party_id, role)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (tenant_id, cp_id, cp.get("role", "curator")),
            )
            # `mode='owned'`, not a fourth "observed" value that never belonged to
            # `domain.ProfileMode`. This *is* the curator's own account — discovery
            # found it because it is the surface that made this party exist in the
            # first place, exactly the same relationship an artist has to their own
            # Spotify page. `match_basis='measured'` already carries the "an
            # algorithm found this, nobody asserted it" distinction; `mode` answers
            # a different question (who runs the account), and the answer here is
            # always "they do." Measured live 2026-08-09, before this fix: 18 of 21
            # `presence` rows carried the illegal `'observed'` value this line used
            # to write — see migration `014`'s `presence_mode_known` CHECK, added
            # `NOT VALID` so those 18 pre-existing rows are not silently rewritten.
            cur.execute(
                """INSERT INTO presence (tenant_id, subject_kind, subject_id, platform,
                                         mode, handle, url, state, match_basis,
                                         confidence)
                   VALUES (%s, 'party', %s, %s, 'owned', %s, %s, 'present',
                           'measured', 1.0)
                   ON CONFLICT (tenant_id, subject_kind, subject_id, platform)
                   DO NOTHING""",
                (tenant_id, cp_id, platform, cp["name"], cp.get("url", "")),
            )

            # The profile text is evidence, so it is a document rather than a column:
            # it is what an embedding was computed from, and a fact whose basis has
            # been thrown away cannot be argued with later.
            body = cp["profile_text"]
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'text/plain', 'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, cp_id, lead["id"], platform, cp.get("url", ""),
                 f"{cp['name']} — curator profile", body, content_hash(body)),
            )

            follow_on.append({
                "kind": "embed_party", "adapter": platform, "platform": platform,
                "party_id": str(cp_id), "scope_kind": "party",
                "target": str(cp_id),
                "target_hash": f"embed_party:{cp_id}",
                "reason": f"curator discovered via {platform}, not yet searchable",
                "score": 0.7,
            })

    return fleet.Outcome(
        summary=f"{written} new curator(s) from {harvest.summary}",
        calls=harvest.calls, leads=len(follow_on), follow_on=follow_on,
        dropped=max(0, len(harvest.counterparties) - cap),
    )


#: Find people worth pitching this artist to, and queue each one for embedding.
#: Discovery is bounded by `party_budget.max_leads_per_run` rather than by a constant, so
#: an operator can widen or narrow it per artist without a deploy. A frontier with no
#: ceiling is how one artist's expansion consumes an afternoon of somebody's free tier.
#: Split into fetch/write because `adapter.discover_counterparties` is an HTTP call — see
#: `fleet.NetworkAgent`.
find_counterparties = fleet.NetworkAgent(fetch=_fetch_find_counterparties,
                                         write=_write_find_counterparties)


def profile_party(conn: psycopg.Connection, lead: dict[str, Any],
                  gate: spend.Gate) -> fleet.Outcome:
    """Compose what we know about one of our own artists into text, then queue embedding.

    A counterparty's profile comes from the outside — playlist titles somebody else wrote.
    Ours has to be composed from the catalogue, because nobody has written about these
    artists yet; that is the problem the product exists to solve.

    Composed from structured rows rather than invented prose, and it reads a little flat
    as a result. That is the correct trade: every clause here is a row somebody can go and
    check, and a fluent biography generated by a model would be an `inferred` fact wearing
    the costume of an `asserted` one.
    """
    party_id = lead.get("party_id") or lead["target"]
    tenant_id = lead["tenant_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT name, artist_type, kind FROM party WHERE tenant_id = %s AND id = %s",
                    (tenant_id, party_id))
        party = cur.fetchone()
        if party is None:
            raise fleet.LeadFailed("the party is gone", permanent=True)

        cur.execute(
            """SELECT r.title, r.isrc FROM recording r
                 JOIN party_credit c ON c.tenant_id = r.tenant_id AND c.subject_id = r.id
                                     AND c.subject_kind = 'recording'
                WHERE c.tenant_id = %s AND c.party_id = %s ORDER BY r.title""",
            (tenant_id, party_id),
        )
        recordings = cur.fetchall()

        cur.execute(
            """SELECT DISTINCT value_text FROM party_fact
                WHERE tenant_id = %s AND party_id = %s AND dimension = 'genre'
                  AND status = 'live'""",
            (tenant_id, party_id),
        )
        genres = [r["value_text"] for r in cur.fetchall()]

        cur.execute(
            """SELECT platform, metric, value FROM party_metric
                WHERE tenant_id = %s AND party_id = %s
                ORDER BY observed_at DESC LIMIT 5""",
            (tenant_id, party_id),
        )
        metrics = cur.fetchall()

    parts = [f"{party['name']} is a {party['artist_type'] or party['kind']}."]
    if genres:
        parts.append(f"Genres: {', '.join(genres)}.")
    if recordings:
        parts.append("Recordings: "
                     + "; ".join(f"{r['title']}" for r in recordings[:20]) + ".")
    if metrics:
        parts.append("Audience: " + "; ".join(
            f"{m['metric']} {int(m['value'])} on {m['platform']}" for m in metrics) + ".")
    if len(parts) == 1:
        # Name and type alone is not a profile, and embedding it would put the artist
        # somewhere arbitrary in the vector space rather than nowhere. Nowhere is better.
        raise fleet.LeadFailed(
            "nothing known about this artist yet beyond a name — map a source first",
            permanent=False)

    body = " ".join(parts)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'internal', '', %s, %s, %s, 'text/plain', 'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"],
                 f"{party['name']} — composed profile", body, content_hash(body)),
            )

    return fleet.Outcome(
        summary=f"profiled {party['name']} from {len(recordings)} recording(s)",
        documents=1,
        follow_on=[{
            "kind": "embed_party", "adapter": "internal", "party_id": str(party_id),
            "scope_kind": "party", "target": str(party_id),
            "target_hash": f"embed_party:{party_id}",
            "reason": "profile composed, not yet searchable", "score": 0.7,
        }],
    )


def _fetch_embed_party(conn: psycopg.Connection, lead: dict[str, Any],
                       gate: spend.Gate) -> dict[str, Any]:
    """Read the party's most recent profile text and embed it. `embed_batch` calls an
    embedding provider over the network, so nothing here may run inside a transaction.
    """
    party_id = lead.get("party_id") or lead["target"]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT body FROM party_document
                WHERE tenant_id = %s AND party_id = %s AND body != ''
                ORDER BY fetched_at DESC LIMIT 1""",
            (lead["tenant_id"], party_id),
        )
        row = cur.fetchone()
    if row is None:
        raise fleet.LeadFailed("nothing written about this party to embed",
                               permanent=True)

    provider = embed.load()
    vectors, cost = embed.embed_batch(gate, provider, [row["body"]])
    return {"body": row["body"], "vector": vectors[0], "cost": cost, "model": provider.model}


def _write_embed_party(conn: psycopg.Connection, lead: dict[str, Any], gate: spend.Gate,
                       prepared: dict[str, Any]) -> fleet.Outcome:
    """Write `party.profile_embedding` and the model beside it, which is what R1's index
    prefix filters on. A party with an embedding and no model is unsearchable by
    construction — migration 009 has a CHECK saying so — so the two are written together.

    Nothing here does network I/O, so `fleet.work_once` runs it inside the same
    transaction as `agent_run` and the lead's completion.
    """
    party_id = lead.get("party_id") or lead["target"]
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE party SET profile_embedding = %s::VECTOR(1024), embedding_model = %s
                WHERE tenant_id = %s AND id = %s""",
            (prepared["vector"].literal(), prepared["model"], lead["tenant_id"], party_id),
        )

    return fleet.Outcome(summary=f"embedded {prepared['model']}", facts=0, calls=1,
                         tokens_in=embed.estimate_tokens([prepared["body"]]),
                         cost_usd=prepared["cost"])


#: Make one party findable by similarity. Split into fetch/write because `embed_batch`
#: calls an embedding provider — see `fleet.NetworkAgent`.
embed_party = fleet.NetworkAgent(fetch=_fetch_embed_party, write=_write_embed_party)


class HistoryExpired(RuntimeError):
    """A replay asked for an instant the cluster no longer retains.

    Its own type rather than a `ValueError`, because callers must be able to tell it
    apart from a malformed `as_of` and say something different: one is "that is not a
    timestamp", the other is "that was a timestamp, and the answer used to exist".
    A console rendering the second as the first tells an operator their input was wrong
    when the truth is that they waited too long.
    """


#: The two ways CockroachDB says "that is before the GC threshold", captured verbatim
#: from this cluster on 2026-08-13 by asking for `-24h` and `-72h` respectively:
#:
#:     batch timestamp 1786548125.584370651,0 must be after replica GC threshold 178657…
#:     error in retrieving descs between 1786375325.763722825,0, 1786460047.883410335,2
#:
#: The second does not mention GC at all — it surfaces as a descriptor lookup failing,
#: because past the threshold the *table's own schema history* is gone too, and that is
#: what the planner misses first.
_GC_BOUNDARY_SIGNATURES = (
    "must be after replica gc threshold",
    "error in retrieving descs between",
)


def _is_gc_boundary(exc: BaseException) -> bool:
    """Whether this error is the GC window and not something else.

    Matching on message text is fragile and it is worth being explicit about why it is
    the right fragility here. CockroachDB reports both of these as generic errors with no
    distinguishing SQLSTATE — `must be after replica GC threshold` arrives as the same
    class as a dozen unrelated failures — so there is no code to switch on, and the text
    is the only signal that exists.

    The failure mode of getting it wrong is deliberately asymmetric. If a future version
    rewords the message, this returns False, the original error propagates unchanged, and
    a caller sees a database error instead of a tidy one — noisy, and correct. What it
    will not do is mistake some *other* failure for an expired history and tell somebody
    their audit trail has aged out when it has not. Both signatures are anchored to
    phrases that mean this and nothing else, and the day one stops matching is a day this
    gets louder rather than quieter.
    """
    return any(sig in str(exc).lower() for sig in _GC_BOUNDARY_SIGNATURES)


def shortlist_as_of(conn: psycopg.Connection, tenant_id: str, party_id: str, *,
                    as_of: str = "", limit: int = 12) -> list[dict[str, Any]]:
    """R1's vector search, read-only, optionally against the index as it was.

    A separate function rather than a parameter on `shortlist`, for a reason that is not
    tidiness. `shortlist` runs inside `conn.transaction()` because it *writes* — it
    increments `lesson.hit_count` for every lesson the rerank consulted. A read-write
    transaction cannot carry `AS OF SYSTEM TIME`; the two are mutually exclusive by
    construction, in CockroachDB and in the concept. So the historical view has to be a
    read, and saying so in the function's name is better than a flag that silently
    disables the write half.

    What survives is the part the demo is about: the same `party@party_shortlist` vector
    search, the same four equality predicates on the index prefix, the same hint and the
    same `<=>` ordering. What is dropped is the lesson rerank and the `presence` subquery
    — neither is a vector operation, and leaving them out keeps this a plain read that
    cannot take a lock or spend anything.

    `as_of` is interpolated into the SQL rather than bound as a parameter because
    CockroachDB requires `AS OF SYSTEM TIME` to be a constant expression at plan time; a
    placeholder is rejected. **It is therefore validated against a strict pattern first,**
    and anything else raises — this is reachable from a public endpoint, so the
    alternative is a SQL injection with a database-shaped hole in it.

    Two shapes are accepted, and the second is the one that matters:

      * **relative** — `-30m`, for a person moving a scrubber. Approximate by nature and
        that is fine, because a human dragging a control does not mean a precise instant.
      * **absolute** — a hybrid logical clock reading like `1786644836824776765.0000000000`,
        which is what `thread.decided_at_hlc` stores and what a *replay* needs.
        `025_decision_provenance.sql` argues at length why an HLC and not a timestamp;
        the short version is that several writes share a millisecond, so a wall clock
        reconstructs a neighbourhood and an HLC reconstructs the instant. Answering "why
        did you email this person" with a ranking that is nearly the one that caused it
        is not an answer, it is a plausible fabrication.

    Both patterns are anchored and admit only digits, a minus, a dot and one trailing
    unit letter, so neither can carry a quote, a semicolon or whitespace into the
    statement. That property is the whole security argument and is asserted by tests
    rather than left to inspection.
    """
    if as_of and not (re.fullmatch(r"-\d{1,4}(s|m|h)", as_of)
                      or re.fullmatch(r"\d{1,19}(\.\d{1,10})?", as_of)):
        raise ValueError(
            f"{as_of!r} is neither a relative window like '-30m' nor a cluster logical "
            "timestamp like '1786644836824776765.0000000000'")
    clause = f"AS OF SYSTEM TIME '{as_of}'" if as_of else ""

    with conn.cursor() as cur:
        cur.execute(
            "SELECT profile_embedding, embedding_model FROM party "
            "WHERE tenant_id = %s AND id = %s", (tenant_id, party_id))
        artist = cur.fetchone()
        if artist is None or artist["profile_embedding"] is None:
            return []

        try:
            cur.execute(
                f"""SELECT id, name,
                           profile_embedding <=> %s::VECTOR(1024) AS distance
                      FROM party@party_shortlist {clause}
                     WHERE tenant_id = %s
                       AND embedding_model = %s
                       AND party_class = 'counterparty'
                       AND contact_state = 'contactable'
                     ORDER BY profile_embedding <=> %s::VECTOR(1024)
                     LIMIT %s""",
                (str(artist["profile_embedding"]), tenant_id, artist["embedding_model"],
                 str(artist["profile_embedding"]), limit),
            )
        except psycopg.Error as exc:
            # The one failure this function must never paper over. Time travel is bounded
            # by `gc.ttlseconds`, and past that boundary the MVCC versions are collected
            # and the answer is genuinely gone.
            #
            # Retrying without the clause would "work": it returns rows, the shape is
            # right, the page renders. It would also be the current ranking presented as
            # the historical one — a true answer to a question nobody asked, offered as
            # the justification for having emailed somebody. There is no failure mode in
            # this codebase worse than that one, which is why this is a raise and not a
            # fallback, and why the message says the history is gone rather than that
            # something went wrong.
            if _is_gc_boundary(exc):
                raise HistoryExpired(
                    f"the memory at {as_of!r} has passed the cluster's garbage-collection "
                    f"window and cannot be reconstructed. `gc.ttlseconds` bounds how far "
                    f"back this can see, and the threshold only moves forward — raising "
                    f"it does not restore history already collected.") from exc
            raise
        return list(cur.fetchall())


def shortlist_with_instant(conn: psycopg.Connection, tenant_id: str, party_id: str, *,
                           limit: int = 12) -> tuple[Any, list[dict[str, Any]]]:
    """The live shortlist, and the instant it was read, provably the same instant.

    `shortlist_as_of` can replay a ranking given an instant; this is how an instant worth
    replaying gets created in the first place. It returns
    `(cluster_logical_timestamp, rows)` for `outreach.open_thread` to store, so that
    "why did you email this person" has an exact coordinate to answer from.

    **Both statements run inside one transaction, and that is the entire reason this
    function exists rather than two calls at the call site.** CockroachDB gives every
    statement in a transaction the same snapshot timestamp, so `cluster_logical_timestamp()`
    read here *is* the timestamp the shortlist was computed at. Called separately under
    autocommit they would be two instants a few milliseconds apart, and a replay would
    then reconstruct the index as it was very slightly before or after the read that
    actually happened — which is the same class of near-miss `025_decision_provenance.sql`
    rejects wall-clock timestamps for. Near enough is not an audit trail.

    Read-only and unpriced, like `shortlist_as_of` and unlike `shortlist`: no lesson
    rerank, so no `hit_count` write, so no `Gate`. A justification that cost money to
    record would be a justification somebody eventually skips recording.

    The rank returned to the caller is the position in this list, one-based, which is
    what `thread.decided_rank` means and what `outreach.justification` compares against.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT cluster_logical_timestamp() AS hlc")
            hlc = cur.fetchone()["hlc"]
        rows = shortlist_as_of(conn, tenant_id, party_id, limit=limit)
    return hlc, rows


def rank_of(rows: list[dict[str, Any]], party_id: str) -> tuple[int, float] | None:
    """`(rank, distance)` for `party_id` in a shortlist, or `None` if it is not there.

    One-based, because `decided_rank` is a position a human would say out loud — "they
    were third" — and because `025`'s CHECK refuses zero for exactly that reason.

    `None` is the honest answer for a counterparty an operator opened a thread with while
    they were not on the shortlist at all, which is a legitimate thing to do and must not
    be recorded as rank 1. The caller writes no decision in that case, and the thread
    reads as hand-opened — which is what it was.
    """
    for i, row in enumerate(rows, start=1):
        if str(row["id"]) == str(party_id):
            return i, float(row["distance"])
    return None


def shortlist(conn: psycopg.Connection, tenant_id: str, party_id: str, *,
              gate: spend.Gate, limit: int = 20) -> list[dict[str, Any]]:
    """R1 — given one of our artists, who should we take them to?

    The whole point of the vector index, and the reason `PLATFORM-SPEC §6` says this
    database rather than a simpler one. Every predicate is equality on a prefix column:

        tenant_id = $1 AND embedding_model = $2
        AND party_class = 'counterparty' AND contact_state = 'contactable'

    so the plan is a `vector search` node with `prefix spans`, not a full scan with a
    post-filter. Drop `contact_state` from the query and it still returns rows — it just
    starts returning people we are already talking to, which is the failure the column
    exists to make impossible.

    The query vector is the artist's own profile embedding, so this is genuinely
    "who resembles the audience this act already has", not a keyword match on genre.

    Two passes. R1 is the vector search above — every predicate an equality on a prefix
    column, resolving to a `vector search` node with `prefix spans`. R2 is
    `lessons.retrieve_for`, and the rerank it feeds is what makes this function's answer
    depend on what happened last time. A row comes back carrying `adjusted` and
    `applied`, and `applied` names every lesson that moved it, because the console's
    inspector has to be able to say why.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT profile_embedding::STRING AS vec, embedding_model, name
                 FROM party WHERE tenant_id = %s AND id = %s""",
            (tenant_id, party_id),
        )
        artist = cur.fetchone()
    if artist is None or not artist["vec"]:
        raise fleet.LeadFailed(
            "this artist has no profile embedding yet — embed them first",
            permanent=True)

    # R1, R2 and the `hit_count` increment commit together. `db.py` opens the connection
    # in autocommit, so without this the three are three separate commits and a crash
    # between computing `ranked` and running the UPDATE loses the increment silently —
    # and `hit_count` is not telemetry, it is how an operator decides which lessons have
    # earned their place, so an undercount is a defect, not noise. No network call sits
    # inside this span — `retrieve_for` takes an already-computed vector literal and does
    # not embed — so the transaction holds no locks across I/O.
    with conn.transaction():
        # `presence` is polymorphic and a party can have several rows in it — Spotify,
        # Deezer, YouTube. A `LEFT JOIN presence` here would multiply the result: a party
        # with three surfaces becomes three rows, `LIMIT` returns fewer distinct parties
        # than asked for, and the same party can appear twice in the rerank. The CTE runs
        # the vector search alone, with nothing joined to it, so no join can steer the
        # planner away from the index; `url` is then a scalar subquery, which by
        # construction returns at most one row, so it cannot duplicate a party. Do not
        # "simplify" this back into a join — that is the bug this shape exists to avoid.
        # `ORDER BY pr.url LIMIT 1` is arbitrary but deterministic, so the same shortlist
        # renders the same surface on every load rather than whichever one the join order
        # happened to pick.
        #
        # `party@party_shortlist` is an explicit index hint, added after `test_vector_plans.py`
        # caught this exact query planning *without* a `vector search` node on the live
        # cluster despite being join-free. The competitor that actually won: `party_aliases_of`
        # — `(tenant_id, alias_of)`, added by migration `012`, *after* this query was
        # written. Nothing about this query changed; a later, unrelated migration added
        # another tenant-prefixed index on `party`, and CockroachDB's cost-based optimizer
        # started preferring it for a tenant with few matching rows. Any tenant-prefixed
        # secondary index on this table is a candidate to win the same way — `party_by_class`
        # and the `(tenant_id, slug)` unique index are two more that have — so the exposure
        # is not specific to `party_aliases_of`, only first demonstrated by it. That is a
        # second, independent way the planner can "abandon the index" beyond the
        # join/range/subquery cases `docs/reference/COCKROACHDB-AI.md` documents: no join
        # is involved, so the CTE shape alone does not prevent it, and it can be triggered
        # by a migration to a table this function does not otherwise touch. `migration 009`
        # built `party_shortlist` so R1 always runs through it; the hint makes that the
        # actual guarantee instead of a hope the cost model still shares the intent after
        # the next migration lands. Verified: reintroducing a join *inside* the CTE with
        # this hint present does not silently plan around it — CockroachDB refuses outright
        # (`index "party_shortlist" cannot be used for this query`), so the hint converts a
        # silent full/alternate-index scan into a hard failure at plan time, strictly louder
        # than before. `test_vector_plans.py` also asserts, without a database, that this
        # CTE's body contains no `JOIN` — the guard that failure mode depends on, not on
        # CockroachDB continuing to reject the combination.
        with conn.cursor() as cur:
            cur.execute(
                """WITH shortlisted AS (
                        SELECT id, name, contact_state,
                               profile_embedding <=> %s::VECTOR(1024) AS distance
                          FROM party@party_shortlist
                         WHERE tenant_id = %s
                           AND embedding_model = %s
                           AND party_class = 'counterparty'
                           AND contact_state = 'contactable'
                         ORDER BY profile_embedding <=> %s::VECTOR(1024)
                         LIMIT %s
                    )
                    SELECT s.id, s.name, s.contact_state, s.distance,
                           (SELECT pr.url FROM presence pr
                             WHERE pr.tenant_id = %s AND pr.subject_kind = 'party'
                               AND pr.subject_id = s.id
                             ORDER BY pr.url LIMIT 1) AS url
                      FROM shortlisted s
                     ORDER BY s.distance""",
                (artist["vec"], tenant_id, artist["embedding_model"], artist["vec"],
                 SHORTLIST_CANDIDATES, tenant_id),
            )
            candidates = [dict(row) for row in cur.fetchall()]

        # R2. The second pass, and the reason `SCOPE-RESET §1` puts the party at the
        # root: without it this function returns the same answer on the hundredth
        # campaign as on the first.
        applicable = lessons.retrieve_for(
            conn, tenant_id,
            query_vector_literal=artist["vec"],
            model=artist["embedding_model"],
            candidate_ids=[str(row["id"]) for row in candidates],
        )
        ranked = lessons.rerank(candidates, applicable)[:limit]

        # `hit_count` is what tells an operator which lessons earn their place.
        # Incremented here rather than in the rerank, because the rerank is pure and
        # must stay that way.
        spent = {entry["lesson_id"] for row in ranked for entry in row["applied"]}
        if spent:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lesson SET hit_count = hit_count + 1 "
                    "WHERE tenant_id = %s AND id = ANY(%s)",
                    (tenant_id, list(spent)),
                )

    return ranked


#: How a closed thread's final state becomes a lesson's sign and strength.
#:
#: `closed_no_reply` is deliberately weaker than `closed_lost` and not merely negative:
#: silence is not a no. It is also the outcome most likely to be *our* fault — a bad
#: address, a pitch sent in August — so letting it demote a counterparty as hard as an
#: explicit decline would teach the shortlist to abandon people who never heard from us.
#:
#: Confidence is low across the board because one thread is one observation. It is the
#: number that should rise as outcomes repeat, via `supersedes_id`; §10 of the spec has
#: that as future work and it is not invented here.
_OUTCOME: dict[str, tuple[float, float, str]] = {
    "closed_won":      (1.0, 0.45, "agreed to work with us after"),
    "closed_lost":     (-1.0, 0.45, "declined or fell through after"),
    "closed_no_reply": (-0.35, 0.30, "never replied to"),
}


def _fetch_distil_lesson(conn: psycopg.Connection, lead: dict[str, Any],
                         gate: spend.Gate) -> dict[str, Any]:
    """Read a closed thread and turn it into one sentence, then embed that sentence.

    **The sentence is composed, not generated.** There is no language model in this
    repository, and reaching for one here would be the wrong instinct even if there were:
    a lesson is retrieved by similarity and then *arithmetically* reweights a shortlist,
    so a hallucinated clause does not read as a mistake, it reads as a ranking. What can
    be said truthfully from the thread's own columns is said, and nothing else is.

    The embedding call is why this is a `NetworkAgent` — see `_write_distil_lesson`.
    """
    with conn.cursor() as cur:
        cur.execute(
            # `channel` lives on `campaign`, not on `thread` — a thread inherits the
            # channel of the campaign that opened it, and the join is inner because
            # `thread.campaign_id` is NOT NULL: a thread without a campaign is not a
            # state this schema can represent.
            """SELECT t.id, t.state, t.reason, t.created_at, t.closed_at,
                      t.campaign_id, c.channel,
                      p.id AS party_id, p.name AS party_name,
                      (SELECT count(*) FROM message m
                        WHERE m.tenant_id = t.tenant_id AND m.thread_id = t.id) AS messages
                 FROM thread t
                 JOIN campaign c ON c.tenant_id = t.tenant_id AND c.id = t.campaign_id
                 JOIN party p ON p.tenant_id = t.tenant_id AND p.id = t.counterparty_id
                WHERE t.tenant_id = %s AND t.id = %s""",
            (lead["tenant_id"], lead["target"]),
        )
        thread = cur.fetchone()

    if thread is None:
        raise fleet.LeadFailed("the thread this lesson is about is gone", permanent=True)
    shape = _OUTCOME.get(thread["state"])
    if shape is None:
        # Reached only if `advance` queued this for a state that is not terminal, which
        # would be a bug in `outreach.CLOSED` rather than a transient fault. Permanent, so
        # it parks for a human instead of retrying four times against the same row.
        raise fleet.LeadFailed(
            f"thread is {thread['state']}, which is not an outcome to learn from",
            permanent=True)

    valence, confidence, verb = shape
    channel = thread["channel"] or "outreach"
    text = f"{thread['party_name']} {verb} a {channel} approach."
    if thread["reason"]:
        text += f" Recorded reason: {thread['reason']}."

    return {
        "prepared": lessons.prepare(text, gate=gate),
        "text": text,
        "party_id": str(thread["party_id"]),
        "valence": valence,
        "confidence": confidence,
        "evidence": {
            "thread_id": str(thread["id"]),
            "campaign_id": str(thread["campaign_id"]) if thread["campaign_id"] else "",
            "final_state": thread["state"],
            "channel": channel,
            "messages": int(thread["messages"]),
            "opened_at": thread["created_at"].isoformat() if thread["created_at"] else "",
            "closed_at": thread["closed_at"].isoformat() if thread["closed_at"] else "",
        },
        "tokens": embed.estimate_tokens([text]),
    }


def _write_distil_lesson(conn: psycopg.Connection, lead: dict[str, Any], gate: spend.Gate,
                         prepared: dict[str, Any]) -> fleet.Outcome:
    """Insert the lesson, in the transaction that also commits this run and the lead.

    This is the sentence the submission actually rests on: the lesson, the `agent_run`
    row and the lead's completion land together or not at all, so a lesson can never be
    lost by a crash that still marked the work done — and the work is not done until the
    memory is. `write_prepared` opens no transaction precisely so this holds.

    Scoped to the counterparty. A thread tells us about the person we talked to; it does
    not license a claim about the channel or about counterparties in general, and
    `lessons.applies` would let a `channel`-scoped row touch every candidate in a
    shortlist. Broader scopes are earned by repetition, not by one conversation.
    """
    lessons.write_prepared(
        conn, lead["tenant_id"], prepared=prepared["prepared"],
        scope_kind="party", scope_id=prepared["party_id"], text=prepared["text"],
        valence=prepared["valence"], confidence=prepared["confidence"],
        evidence=prepared["evidence"])

    # Counted as a fact because that is what it is — something now known that was not
    # known before — and `/runs` reads these columns to show an operator what a run did.
    return fleet.Outcome(summary=f"learned: {prepared['text'][:80]}", facts=1, calls=1,
                         tokens_in=prepared["tokens"])


#: Turn a closed thread into a lesson the next shortlist can read. This is the write half
#: of the memory loop — `shortlist` was reading `lesson` and nothing was writing it.
distil_lesson = fleet.NetworkAgent(fetch=_fetch_distil_lesson, write=_write_distil_lesson)


# ------------------------------------------------------------ analyse_recording
#
# The agent the masters work exists for. Everything above measures somebody else's
# metadata; this one listens to the record.
#
# It is a `NetworkAgent` for the usual reason and one extra. The usual: it fetches
# tens of megabytes over HTTP and must not do that inside the transaction that writes
# `agent_run` and completes the lead. The extra: it is the first agent here whose fetch
# writes to the local filesystem, and the temporary file is removed in a `finally` that
# runs before `write` is ever called — so a worker killed mid-lease leaks a file in
# /tmp at worst, and the lease brings the lead back for someone else to fetch again.
#
# **This agent does not run in the web Lambda.** It needs ffmpeg on PATH and numpy,
# neither of which is in the function's bundle, and `requirements-worker.txt` exists to
# say so. Drain it with:
#
#     python -m spindle.ingest --drain-only --kinds analyse_recording

#: Read the master in chunks rather than in one call. A 200MB master read with a bare
#: `.read()` is 200MB resident in a worker that may be sharing a machine with others.
_DOWNLOAD_CHUNK = 1024 * 1024


#: A style is kept as a search term if it is within this fraction of the top score. The
#: classifier is multi-label — 400 independent sigmoids, not a softmax — so a record can
#: genuinely be House *and* Deep House *and* Progressive House, and a tight cluster is
#: the model saying so rather than the model being unsure.
#:
#: Measured on Hallow Youth's "Losing Sleep": Tropical House 0.310, House 0.290, Deep
#: House 0.288, Progressive House 0.286, Electro House 0.168. Taking the argmax alone
#: would pick "Tropical House" over "House" on a 0.02 margin and throw away three
#: perfectly good playlist queries; this keeps the first four and drops the fifth.
_TERM_RATIO = 0.6

#: An absolute floor underneath the ratio, so that a track the model barely recognises
#: does not contribute four confident-looking terms that are all noise.
_TERM_FLOOR = 0.15

#: Deezer's playlist search is the consumer and more queries cost more calls, so this is
#: what the tempo path already caps at.
_MAX_TERMS = 4


def _classifier_terms(genre: dict[str, Any] | None) -> list[str]:
    """The classifier's labels as playlist search terms, or `[]` if it did not run.

    Preferred over the tempo-derived terms wherever both exist, and it is worth being
    precise about why rather than treating them as interchangeable. The tempo path says
    "125 BPM records are often house" — a lookup table keyed on a number. The classifier
    says "this record is house" — a model that listened to it. Both are `inferred` and
    only one is about the record.
    """
    if not genre or not genre.get("styles"):
        return []
    scores = genre["styles"]
    best = scores[0]["p"]
    cutoff = max(_TERM_FLOOR, best * _TERM_RATIO)
    kept = [s["label"].split("---")[-1].strip()
            for s in scores if s["p"] >= cutoff]
    return list(dict.fromkeys(kept))[:_MAX_TERMS]


def _search_terms(value: str) -> list[str]:
    """A stored fact value, as the things you would actually type into a playlist search.

    Three shapes reach this, and flattening them here rather than at write time keeps the
    stored fact faithful to what produced it:

      * `Electronic---Tropical House` — Discogs' own `Parent---Style` separator. **Only
        the style is returned.** "Electronic" describes a third of Deezer and would
        return the same playlists for a techno record and an ambient one; "Tropical
        House" is what a curator actually curates. The parent is already stored as its
        own `genre` fact for anyone who wants it.
      * `house, melodic house, progressive house` — the comma-joined list
        `analyse_recording` writes for `style_terms`.
      * `Dance` — a bare platform label.
    """
    parts = [chunk.split("---")[-1].strip()
             for chunk in value.split(",") if chunk.strip()]
    return [p for p in parts if p]


def _fetch_analyse_recording(conn: psycopg.Connection, lead: dict[str, Any],
                             gate: spend.Gate) -> dict[str, Any]:
    """Download the master, check it is the file we were promised, and listen to it.

    No spend is recorded and none is gated. Every other network agent here calls a
    metered provider; this one calls S3 with a URL it signed itself, and the cost is
    egress measured in cents per gigabyte rather than tokens. Charging a token budget
    for it would put a number in `agent_run` that means nothing.

    The hash check is the part worth reading. `assets.confirm` verifies that an object
    exists and that its size matches what the browser promised, and deliberately stops
    there — checking the digest would mean downloading the file into a Lambda, which is
    exactly what that design avoids. This agent downloads the file anyway, so hashing
    the stream on its way past costs one `hashlib` update per chunk. That is where an
    `asserted` content hash becomes a `measured` one, and where a corrupted object gets
    caught by something other than a human noticing the track sounds wrong.
    """
    import hashlib
    import os
    import tempfile
    import urllib.error
    import urllib.request

    from spindle import assets, audio, storage

    tenant_id = lead["tenant_id"]
    asset_id = lead["target"]

    asset = assets.get(conn, tenant_id, asset_id)
    if asset is None:
        raise fleet.LeadFailed("the asset this lead is about is gone", permanent=True)
    if asset["state"] != "stored":
        # Queued before the upload was confirmed, or the confirm failed. Not permanent:
        # the operator may still finish the upload, and the lead's backoff is the right
        # amount of patience for that.
        raise fleet.LeadFailed(f"asset {asset_id} is {asset['state']}, not stored")

    cfg = settings_mod.load()
    try:
        store = storage.load(cfg.masters_bucket, cfg.region)
    except storage.StorageUnconfigured as exc:
        # A worker with no bucket configured cannot do this job and will not be able to
        # on the next attempt either. Park it rather than burn four retries.
        raise fleet.LeadFailed(str(exc), permanent=True) from exc

    url = store.presign_get(asset["object_key"])
    digest = hashlib.sha256()
    size = 0
    path = ""

    try:
        handle = tempfile.NamedTemporaryFile(suffix=".audio", delete=False)
        path = handle.name
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                while chunk := response.read(_DOWNLOAD_CHUNK):
                    size += len(chunk)
                    if size > assets.MAX_ASSET_BYTES:
                        raise fleet.LeadFailed(
                            f"the object at {asset['object_key']} is larger than the "
                            f"{assets.MAX_ASSET_BYTES} byte ceiling", permanent=True)
                    digest.update(chunk)
                    handle.write(chunk)
        finally:
            handle.close()

        measured_hash = digest.hexdigest()
        if measured_hash != asset["content_hash"]:
            # The bytes in the bucket are not the bytes that were uploaded. Permanent
            # because retrying downloads the same wrong object, and loud because every
            # fact already measured from this asset is now suspect.
            raise fleet.LeadFailed(
                f"the object at {asset['object_key']} hashes to {measured_hash[:16]}… "
                f"and the row says {asset['content_hash'][:16]}…. Something replaced "
                f"the object, or the upload was never the file it claimed to be.",
                permanent=True)

        try:
            shape = audio.probe(path)
            facts = audio.measure_file(path, basis=f"recording_asset:{asset_id}")
        except audio.Unmeasurable as exc:
            # ffmpeg missing is a worker that was drained without its dependencies, and
            # that is not permanent — it is fixed by installing them and draining again.
            # A file ffmpeg refuses is permanent: the object will not improve.
            raise fleet.LeadFailed(str(exc),
                                   permanent="ffmpeg" not in str(exc)) from exc
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                # A leaked temp file is not worth failing a lead that otherwise
                # succeeded, and /tmp is swept by the machine.
                pass

    return {
        "asset_id": asset_id,
        "recording_id": str(asset["recording_id"]),
        "shape": shape,
        "facts": facts,
        "bytes": size,
        "genre": _classify(cfg, asset),
    }


def _classify(cfg: Any, asset: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the classifier Lambda what genre this is. `None` when there is no classifier.

    `None` is not a fallback and must not be treated as one. When no classifier is
    configured this agent writes **no genre fact at all** and says so in its run summary
    — rather than substituting the tempo-derived style terms and labelling them the same
    way a model's answer would be labelled. Those two are not interchangeable: a tempo
    band says "125 BPM records are often house", and the model says "this record is
    house". `NO FALLBACKS` is precisely about not letting the first quietly stand in for
    the second.

    The invoke is synchronous because the answer is needed to write the row, and it is
    in `fetch` because a two-minute Lambda call inside the write transaction would hold
    it open for two minutes.
    """
    if not cfg.classifier_function:
        return None

    import json

    import boto3
    from botocore.config import Config

    client = boto3.client(
        "lambda", region_name=cfg.region,
        # The classifier's own timeout is 300s and a cold start loads TensorFlow. The
        # botocore default read timeout is 60s, which would abandon a perfectly healthy
        # invocation and retry it — paying for the cold start twice and still failing.
        config=Config(read_timeout=360, connect_timeout=10, retries={"max_attempts": 0}))

    response = client.invoke(
        FunctionName=cfg.classifier_function,
        InvocationType="RequestResponse",
        Payload=json.dumps({"bucket": asset["bucket"], "key": asset["object_key"]}))

    if response.get("FunctionError"):
        raise fleet.LeadFailed(
            f"the classifier failed: "
            f"{response['Payload'].read().decode('utf-8', 'replace')[:300]}")

    result = json.loads(response["Payload"].read())
    if "error" in result:
        # The handler caught something and described it. Not permanent by default: a
        # transient S3 read or an out-of-memory kill on a long master both land here and
        # both are worth one more attempt.
        raise fleet.LeadFailed(f"the classifier refused this master: {result['error']}")
    return result


#: `party_fact.dimension` → which half of `audio.measure`'s return it comes from. The
#: table is the whole point of the split: a style term is `inferred` from a tempo band
#: and a BPM is `measured` off the file, and nothing here can write one as the other
#: because the provenance travels with the dimension rather than with the caller.
_AUDIO_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("bpm", "measured", "bpm"),
    ("brightness_hz", "measured", "brightness_hz"),
    ("bpm_confidence", "inferred", "bpm_confidence"),
)


def _write_analyse_recording(conn: psycopg.Connection, lead: dict[str, Any],
                             gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the shape, the facts, and the basis edges that say what was listened to.

    Every fact here supersedes rather than overwrites, per `SCOPE-RESET §2a` rule 1. A
    remastered track measured again does not erase what the previous master measured —
    "why did we think it was 84 BPM in March" is a question with a right answer, and the
    answer is the row.

    The `fact_basis` edge is the reason migration 016 declined to add
    `party_fact.asset_id`. These are the first rows `fact_basis` has ever held on this
    cluster, and they render in the console's provenance walk with no template change.
    """
    from spindle import assets

    tenant_id = lead["tenant_id"]
    recording_id = prepared["recording_id"]
    asset_id = prepared["asset_id"]
    shape = prepared["shape"]
    facts = prepared["facts"]

    assets.record_shape(conn, tenant_id, asset_id, **shape)

    values: dict[str, Any] = {**facts["measured"], **facts["inferred"]}
    written = 0

    for dimension, provenance, key in _AUDIO_DIMENSIONS:
        written += _supersede_recording_fact(
            conn, tenant_id, recording_id, asset_id,
            dimension=dimension, provenance=provenance,
            value=str(values[key]), basis=facts["basis"])

    # Style terms are a list and are written as one row, not one row per term: they are
    # a single inference from a single tempo band, and splitting them would let a
    # shortlist count "house" and "melodic house" as two independent pieces of evidence
    # for the same claim. Empty is not written at all — `audio.style_terms` abstains on
    # an ambiguous tempo, and an empty row would record that abstention as a finding.
    genre = prepared.get("genre")
    terms = _classifier_terms(genre) or facts["inferred"]["style_terms"]
    if terms:
        written += _supersede_recording_fact(
            conn, tenant_id, recording_id, asset_id,
            dimension="style_terms", provenance="inferred",
            value=", ".join(terms),
            # The source is what tells the two apart, and they are not equivalent: one
            # is a model listening to the record, the other is a lookup table keyed on
            # tempo. Same dimension, same provenance class, different evidence — which
            # is exactly what this column is for.
            basis=(f"{genre['model']}" if _classifier_terms(genre) else facts["basis"]))

    # The classifier's answer, when there was one. `genre` is the parent ("Electronic")
    # and `style` the full Discogs label ("Electronic---House"); the handler reports the
    # style only above its own confidence floor, because the model is reliably better at
    # the genre than at the style within it. Both are `inferred`: a BPM is a property of
    # the waveform, a genre is a model's opinion about a record, and writing the second
    # as `measured` would be the exact provenance laundering `SCOPE-RESET §2a` forbids.
    if genre and genre.get("parent"):
        written += _supersede_recording_fact(
            conn, tenant_id, recording_id, asset_id,
            dimension="genre", provenance="inferred", value=genre["parent"],
            basis=f"{genre['model']} p={genre['confidence']}")
        if genre.get("style"):
            written += _supersede_recording_fact(
                conn, tenant_id, recording_id, asset_id,
                dimension="style", provenance="inferred", value=genre["style"],
                basis=f"{genre['model']} p={genre['confidence']}")

    rivals = facts["inferred"]["tempo_rivals"]
    summary = f"measured {values['bpm']} BPM off the master"
    if rivals and not terms:
        summary += f", abstained on tempo style — rivals at {rivals}"
    if genre is None:
        # Said out loud in the row an operator reads, because the alternative is a track
        # with no genre and no indication of whether that means "unclassifiable" or "no
        # classifier is deployed".
        summary += "; no classifier configured, so no genre was written"
    elif genre.get("parent"):
        summary += f"; {genre.get('style') or genre['parent']} at {genre['confidence']}"
    else:
        summary += "; the classifier recognised nothing above its floor"

    return fleet.Outcome(summary=summary[:200], facts=written)


def _supersede_recording_fact(conn: psycopg.Connection, tenant_id: str,
                              recording_id: str, asset_id: str, *, dimension: str,
                              provenance: str, value: str, basis: str) -> int:
    """One measured claim about a recording, replacing whatever it replaced.

    Returns 1 always — the count is returned rather than assumed so `Outcome.facts`
    stays honest if this ever grows a path that writes nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM party_fact
                WHERE tenant_id = %s AND recording_id = %s AND dimension = %s
                  AND provenance = %s AND status = 'live'
                ORDER BY observed_at DESC LIMIT 1""",
            (tenant_id, recording_id, dimension, provenance))
        previous = cur.fetchone()

        if previous is not None:
            cur.execute(
                """UPDATE party_fact SET status = 'superseded'
                    WHERE tenant_id = %s AND id = %s""",
                (tenant_id, previous["id"]))

        cur.execute(
            """INSERT INTO party_fact
                   (tenant_id, recording_id, dimension, value_text, provenance,
                    status, source, supersedes_id, written_by)
               VALUES (%s, %s, %s, %s, %s, 'live', %s, %s, 'analyse_recording')
            RETURNING id""",
            (tenant_id, recording_id, dimension, value, provenance, basis,
             previous["id"] if previous else None))
        fact_id = cur.fetchone()["id"]

        # The edge migration 016 chose over a new column. `fact_basis` is polymorphic
        # and already rendered by `research._basis`, so this shows up in the console's
        # provenance walk without a template change.
        cur.execute(
            """INSERT INTO fact_basis (subject_kind, subject_id, basis_kind, basis_id)
               VALUES ('fact', %s, 'recording_asset', %s)
               ON CONFLICT DO NOTHING""",
            (fact_id, asset_id))
    return 1


#: Listen to the master and write what can be measured from it. Queued by
#: `routes.masters_confirm` the moment an upload is confirmed, so uploading a file is
#: the only action an operator takes — the analysis follows from the row.
analyse_recording = fleet.NetworkAgent(fetch=_fetch_analyse_recording,
                                       write=_write_analyse_recording)


# ------------------------------------------------------------ index_stations
#
# The stage that gives the label a *standing* counterparty index: when a single is
# ready, the people who could play it are already in the database, already embedded,
# already shortlistable. Everything before this discovered counterparties one artist at
# a time, as a side effect of prospecting; this builds the index up front.
#
# One lead per jurisdiction, and that is the whole reason it is 51 leads and not one.
# A jurisdiction is a natural unit of work: it bounds the fetch, it bounds the write, and
# a state that fails its HTTP call retries on its own backoff without re-fetching the
# other fifty or rolling back their rows.
#
# **No `suggestion` step, and that is a deliberate distinction rather than a shortcut.**
# `suggestion` exists for guesses about *identity* — "is this Deezer profile the same
# person as this party?" — which is what produced thirteen junk parties from a name-match
# harvest in August. There is no identity guess here. The FCC publishes a canonical
# `facility_id` per station, so the station's existence and its licensee are `measured`
# facts with a stable key. The one thing this stage infers, `station_kind`, is written as
# `inferred` and never promoted.

#: How many stations one lead may write. No jurisdiction comes close — the largest holds
#: roughly 150 pitchable NCE licences — so this is a bound on a feed that misbehaves,
#: not a sampling policy. `Outcome.dropped` reports anything past it rather than
#: discarding it quietly.
STATION_CAP = 400


def _fetch_index_stations(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate) -> dict[str, Any]:
    """Pull one jurisdiction's licensed NCE-band FM stations. Network only, no writes.

    The gate is not consulted because there is nothing to charge for: the FM Query is a
    free public service, and `spend.estimate` raises on an unpriced key by design. The
    cost this stage does incur is the embedding of everything it writes, and that is
    charged where it happens — on the `embed_party` leads it queues.
    """
    #: `target` is `<STATE>` or `<STATE>:<band>`. The bare form means the NCE band,
    #: which is what the first 56 leads were seeded as and what they must keep meaning.
    raw = str(lead.get("target") or "").strip().upper()
    if not raw:
        raise fleet.LeadFailed(
            "index_stations needs a jurisdiction in `target`", permanent=True)
    state, _, band = raw.partition(":")
    band = (band or "NCE").lower()

    if state not in fcc.JURISDICTIONS:
        raise fleet.LeadFailed(
            f"{state!r} is not an FCC jurisdiction", permanent=True)
    if band not in fcc.BANDS:
        raise fleet.LeadFailed(
            f"{band!r} is not a band; known: {', '.join(sorted(fcc.BANDS))}",
            permanent=True)

    text = fcc.fetch(state, band=band)
    stations = fcc.dedupe(fcc.parse(text, state))
    return {"state": state, "band": band,
            "stations": fcc.pitchable(stations, band),
            "authorisations": len(stations)}


def _write_index_stations(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write each station as a counterparty, and queue it for embedding.

    Shaped exactly like `_write_find_counterparties`, because a radio station and a
    playlist curator are the same thing to this schema: a party with a role, some facts,
    a document that says who they are, and an embedding computed from that document.
    Migration 009 argues at length that there is no `counterparty` table for precisely
    this reason.

    `kind = 'organisation'` rather than `'person'` — a licence is held by a body, not by
    the music director we eventually write to. The person comes later, as a
    `contact_route` with an `addressee`, which is why 018 put the addressee on the route
    and not on the party.
    """
    tenant_id, state = lead["tenant_id"], prepared["state"]
    stations = prepared["stations"]

    written = 0
    follow_on: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        for st in stations[:STATION_CAP]:
            # The facility ID is the Commission's own key for "this station" and survives
            # a call-sign change, so it is what the slug and the identifier are built on.
            # `dedupe` already fell back to call sign + frequency for the rare row
            # without one; the same fallback is repeated here so the slug stays stable
            # for those rows too.
            fac = st["facility_id"] or f"{st['call_sign']}-{st['frequency_mhz']}"
            slug = f"{repo.slugify(st['call_sign'])}-fcc-{repo.slugify(str(fac))}"[:120]
            freq = f"{st['frequency_mhz']:.1f}" if st["frequency_mhz"] else "FM"
            name = f"{st['call_sign']} {freq} ({st['city']}, {st['state']})"

            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                      contact_state, status)
                   VALUES (%s, %s, %s, 'organisation', 'counterparty', 'contactable',
                           'active')
                   ON CONFLICT (tenant_id, slug) DO NOTHING
                RETURNING id""",
                (tenant_id, slug, name),
            )
            created = cur.fetchone()
            if created is None:
                # Already indexed. Re-running a jurisdiction is the normal case — the
                # register changes — and it must not re-queue an embedding we have
                # already paid for.
                continue
            party_id = created["id"]
            written += 1

            cur.execute(
                """INSERT INTO party_role (tenant_id, party_id, role)
                   VALUES (%s, %s, 'radio_programmer') ON CONFLICT DO NOTHING""",
                (tenant_id, party_id),
            )
            if st["facility_id"]:
                cur.execute(
                    """INSERT INTO party_identifier (tenant_id, party_id, kind, value,
                                                     provenance)
                       VALUES (%s, %s, 'fcc_facility_id', %s, 'measured')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id, st["facility_id"]),
                )
            # The call sign is the *public* name of a station — it is how every other
            # source in the world refers to it, and `party_identifier` is unique on
            # `(tenant_id, kind, value)`, so this is the key any later source joins on
            # rather than parsing a display name. `index_streams` matches on exactly this
            # to enrich a station instead of creating a second party for it.
            cur.execute(
                """INSERT INTO party_identifier (tenant_id, party_id, kind, value,
                                                 provenance)
                   VALUES (%s, %s, 'call_sign', %s, 'measured')
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, st["call_sign"]),
            )

            # Four facts, three transcribed and one guessed, and the guess says so in its
            # own `provenance` column. `fact_one_live_per_dimension` is a partial unique
            # index over (dimension, provenance), so a re-index cannot stack duplicates.
            facts = [
                ("licensee", st["licensee"], "measured"),
                ("market", f"{st['city']}, {st['state']}", "measured"),
                ("frequency_mhz", freq, "measured"),
                ("station_kind", st["station_kind"], "inferred"),
            ]
            for dimension, value, provenance in facts:
                cur.execute(
                    """INSERT INTO party_fact (tenant_id, party_id, dimension,
                                               value_text, provenance, status, source,
                                               written_by)
                       VALUES (%s, %s, %s, %s, %s, 'live', 'fcc_fm_query',
                               'index_stations')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id, dimension, value, provenance),
                )

            # The document is the evidence the embedding is computed from — same rule as
            # a curator's profile text. A vector whose source text has been thrown away
            # cannot be argued with when a shortlist looks wrong.
            body = fcc.profile_text(st)
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'fcc', '', %s, %s, %s, 'text/plain', 'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"],
                 f"{st['call_sign']} — FCC licence record", body, content_hash(body)),
            )

            follow_on.append({
                "kind": "embed_party", "adapter": "fcc", "platform": "fcc",
                "party_id": str(party_id), "scope_kind": "party",
                "target": str(party_id),
                "target_hash": f"embed_party:{party_id}",
                "reason": f"{st['call_sign']} indexed from the FCC register, "
                          "not yet searchable",
                "score": 0.6,
            })

    return fleet.Outcome(
        summary=(f"{written} new {prepared['band']} station(s) in {state} — "
                 f"{prepared['authorisations']} authorisations, "
                 f"{len(stations)} pitchable"),
        calls=1, leads=len(follow_on), follow_on=follow_on,
        dropped=max(0, len(stations) - STATION_CAP),
    )


#: Build the standing radio index, one US jurisdiction at a time, and queue each station
#: for embedding so it is shortlistable the day a record is ready.
index_stations = fleet.NetworkAgent(fetch=_fetch_index_stations,
                                    write=_write_index_stations)


# ------------------------------------------------------------ index_streams
#
# What a station plays, and where its website is — the two things the licence register
# does not know and a shortlist cannot work without.
#
# This stage does something the others do not: it **enriches a party it did not create**.
# A Radio Browser row whose name leads with a call sign is the same station the FCC
# already gave us, and writing a second party for it would put the same broadcaster on a
# shortlist twice. `party_identifier(kind='call_sign')` is the join, which is why
# `index_stations` writes one for every station and why 1,777 were backfilled.
#
# The enrichment has to reach the *vector*, not just a column, or none of it matters.
# `_fetch_embed_party` embeds a party's most recent `party_document`, so this stage
# writes a new document whose prose names the genres and then returns that party's
# `embed_party` lead to the frontier. A genre in a column that never reached an embedding
# would leave the shortlist exactly as wrong as it was.


def _fetch_index_streams(conn: psycopg.Connection, lead: dict[str, Any],
                         gate: spend.Gate) -> dict[str, Any]:
    """One page of one country's streaming stations. Network only, no writes.

    Free, like the FCC feed, so the gate is not consulted — the cost this stage causes is
    the re-embedding it queues, and that is charged where it happens.
    """
    raw = str(lead.get("target") or "").strip()
    if not raw:
        raise fleet.LeadFailed(
            "index_streams needs `<COUNTRY>:<offset>` in `target`", permanent=True)
    country, _, offset_text = raw.partition(":")
    try:
        offset = int(offset_text or 0)
    except ValueError:
        raise fleet.LeadFailed(
            f"{offset_text!r} is not an offset", permanent=True) from None

    page = radiobrowser.stations(country, offset=offset)
    return {"country": country.upper(), "offset": offset, "stations": page}


def _write_index_streams(conn: psycopg.Connection, lead: dict[str, Any],
                         gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Enrich the stations we already know, create the ones we do not, re-embed both."""
    tenant_id = lead["tenant_id"]
    enriched = created = 0
    follow_on: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        for st in prepared["stations"]:
            name = (st.get("name") or "").strip()
            uuid = st.get("stationuuid") or ""
            if not name or not uuid:
                continue
            tags = radiobrowser.genres(st)

            # Does the FCC already know this station? The call sign is the join.
            party_id = None
            sign = radiobrowser.call_sign(name)
            if sign:
                cur.execute(
                    """SELECT party_id FROM party_identifier
                        WHERE tenant_id = %s AND kind = 'call_sign' AND value = %s""",
                    (tenant_id, sign),
                )
                found = cur.fetchone()
                if found is not None:
                    party_id = found["party_id"]

            if party_id is None:
                cur.execute(
                    """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                          contact_state, status)
                       VALUES (%s, %s, %s, 'organisation', 'counterparty',
                               'contactable', 'active')
                       ON CONFLICT (tenant_id, slug) DO NOTHING
                    RETURNING id""",
                    (tenant_id, f"rb-{uuid}"[:120], name[:200]),
                )
                fresh = cur.fetchone()
                if fresh is None:
                    continue      # already indexed from a previous page
                party_id = fresh["id"]
                created += 1
                cur.execute(
                    """INSERT INTO party_role (tenant_id, party_id, role)
                       VALUES (%s, %s, 'radio_programmer') ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id),
                )
                if sign:
                    cur.execute(
                        """INSERT INTO party_identifier (tenant_id, party_id, kind,
                                                         value, provenance)
                           VALUES (%s, %s, 'call_sign', %s, 'asserted')
                           ON CONFLICT DO NOTHING""",
                        (tenant_id, party_id, sign),
                    )
            else:
                enriched += 1

            cur.execute(
                """INSERT INTO party_identifier (tenant_id, party_id, kind, value,
                                                 provenance)
                   VALUES (%s, %s, 'radiobrowser_uuid', %s, 'measured')
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, uuid),
            )

            # Genre, as one row rather than one row per tag: `fact_one_live_per_dimension`
            # allows a single live fact per (dimension, provenance), and a station's
            # genres are one claim about it, not five independent ones.
            if tags:
                cur.execute(
                    """INSERT INTO party_fact (tenant_id, party_id, dimension,
                                               value_text, value_json, provenance,
                                               status, confidence, source, written_by)
                       VALUES (%s, %s, 'genre', %s, %s, 'asserted', 'live', %s,
                               'radio_browser', 'index_streams')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id, ", ".join(tags), Json(tags),
                     0.5 if radiobrowser.musical(tags) else 0.3),
                )

            # The website is a `presence`, not a `contact_route`. A homepage is a surface
            # you can read, which is exactly what `presence` means; a contact route is an
            # endpoint you may *send to*, and conflating the two is how a crawler's URL
            # ends up in a mail merge. The route comes from reading this page, later.
            homepage = (st.get("homepage") or "").strip()
            if homepage:
                cur.execute(
                    """INSERT INTO presence (tenant_id, subject_kind, subject_id,
                                             platform, mode, handle, url, state,
                                             match_basis, confidence)
                       VALUES (%s, 'party', %s, 'web', 'owned', %s, %s, 'present',
                               'asserted', 0.6)
                       ON CONFLICT (tenant_id, subject_kind, subject_id, platform)
                       DO NOTHING""",
                    (tenant_id, party_id, name[:120], homepage[:500]),
                )

            body = radiobrowser.profile_text(st, tags)
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'radio_browser', %s, %s, %s, %s, 'text/plain',
                           'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"], homepage[:500],
                 f"{name} — Radio Browser profile", body, content_hash(body)),
            )

            # Return this party's embedding to the frontier so the genre reaches the
            # vector. An already-completed `embed_party` lead is *requeued* rather than
            # re-inserted, because `(tenant_id, target_hash)` is unique and a second
            # insert would silently do nothing — leaving the station embedded on the
            # text that did not mention what it plays.
            cur.execute(
                """UPDATE lead
                      SET state = 'pending', attempts = 0, owner_agent = NULL,
                          lease_expires_at = NULL, lease_token = NULL,
                          next_action_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND target_hash = %s AND state != 'pending'""",
                (tenant_id, f"embed_party:{party_id}"),
            )
            if cur.rowcount == 0:
                follow_on.append({
                    "kind": "embed_party", "adapter": "radio_browser",
                    "platform": "radio_browser", "party_id": str(party_id),
                    "scope_kind": "party", "target": str(party_id),
                    "target_hash": f"embed_party:{party_id}",
                    "reason": f"{name[:60]} indexed from Radio Browser, not yet "
                              "searchable",
                    "score": 0.6,
                })

    return fleet.Outcome(
        summary=(f"{prepared['country']} +{prepared['offset']}: "
                 f"{created} new station(s), {enriched} enriched with genre"),
        calls=1, facts=created + enriched, leads=len(follow_on), follow_on=follow_on,
    )


#: Give every station a genre and a website, and put the genre where the embedding will
#: find it. Enriches stations `index_stations` already wrote; creates the rest.
index_streams = fleet.NetworkAgent(fetch=_fetch_index_streams,
                                   write=_write_index_streams)


# ------------------------------------------------------------ enrich_genre
#
# The stage that takes genre coverage from a third of the index to most of it.
#
# Sequenced deliberately after `index_streams`: Radio Browser's tags are the better
# source where they exist — they describe what a station *streams today*, not what an
# encyclopaedia recorded — so this stage only ever looks at parties that still have no
# genre, and re-running it is therefore self-limiting rather than a re-fetch of
# everything.
#
# **Work is bucketed by call-sign prefix**, one lead per two-letter prefix, and that
# choice is doing real work. It is deterministic, so a re-run addresses the same
# stations; it is resumable, because the query selects on "has no genre yet" rather than
# on a cursor that a crash could lose; and it is naturally bounded, because a prefix
# holds a few hundred stations rather than eight thousand.

#: Stations one lead will look up. A prefix bucket larger than this is finished by the
#: lead's *next* run rather than in one lease — `GENRE_CAP` divided by `wikipedia.BATCH`
#: is the number of API calls one run makes, and six is a polite ceiling for a service
#: that is answering for free.
GENRE_CAP = 150


def _fetch_enrich_genre(conn: psycopg.Connection, lead: dict[str, Any],
                        gate: spend.Gate) -> dict[str, Any]:
    """Look up formats for one call-sign prefix. Reads the database, writes nothing.

    Reading here is allowed and deliberate — `NetworkAgent.fetch` runs with no
    transaction open, so this is an autocommitted SELECT, not a held lock. Doing the
    selection here rather than in `write` is what lets the expensive part (the HTTP
    round trips) happen outside the transaction that later commits the facts.
    """
    # Any two letters, not `[KWC][A-Z]`. The buckets are derived from call signs that are
    # actually in the index, so a validator narrower than the data rejects real work: five
    # rows carry a leading `D`, `N` or `Z` from an FCC feed artifact — `DWWQZ` is `WWQZ`
    # with a marker the parser took literally — and the first version of this check failed
    # their prefixes permanently. The seeder is the authority on which buckets exist; this
    # only guards against a malformed target.
    prefix = str(lead.get("target") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", prefix):
        raise fleet.LeadFailed(
            f"{prefix!r} is not a two-letter call-sign prefix", permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT pi.value AS call_sign, pi.party_id
                 FROM party_identifier pi
                WHERE pi.tenant_id = %s AND pi.kind = 'call_sign'
                  AND pi.value LIKE %s
                  AND NOT EXISTS (SELECT 1 FROM party_fact f
                                   WHERE f.tenant_id = pi.tenant_id
                                     AND f.party_id = pi.party_id
                                     AND f.dimension = 'genre')
                ORDER BY pi.value
                LIMIT %s""",
            (lead["tenant_id"], f"{prefix}%", GENRE_CAP),
        )
        rows = cur.fetchall()

    if not rows:
        return {"prefix": prefix, "rows": [], "found": {}}

    found = wikipedia.formats([r["call_sign"] for r in rows])
    return {"prefix": prefix, "rows": rows, "found": found}


def _write_enrich_genre(conn: psycopg.Connection, lead: dict[str, Any],
                        gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the formats found, and send each enriched party back for re-embedding.

    A station whose format Wikipedia does not record gets **nothing** — no row, no
    placeholder, no genre guessed from its licensee's name. The absence is the honest
    answer and the shortlist is entitled to see it.
    """
    tenant_id = lead["tenant_id"]
    found: dict[str, str] = prepared["found"]
    written = 0

    with conn.cursor() as cur:
        for row in prepared["rows"]:
            fmt = found.get(row["call_sign"])
            if not fmt:
                continue
            party_id = row["party_id"]

            cur.execute(
                """INSERT INTO party_fact (tenant_id, party_id, dimension, value_text,
                                           value_json, provenance, status, confidence,
                                           source, written_by)
                   VALUES (%s, %s, 'genre', %s, %s, 'asserted', 'live', 0.6,
                           'wikipedia', 'enrich_genre')
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, fmt, Json([fmt])),
            )
            if cur.rowcount == 0:
                continue          # raced with another source; theirs stands
            written += 1

            # The genre has to reach the *vector*, so it is appended to the party's
            # profile text as a new document — `_fetch_embed_party` reads the most
            # recent one — and the existing text is carried forward rather than
            # replaced, because the licence facts in it are still true and still worth
            # embedding.
            cur.execute(
                """SELECT body FROM party_document
                    WHERE tenant_id = %s AND party_id = %s AND body != ''
                    ORDER BY fetched_at DESC LIMIT 1""",
                (tenant_id, party_id),
            )
            previous = cur.fetchone()
            base = previous["body"] if previous else ""
            body = (f"{base}\n\nFormat: {fmt}. This station's programming is "
                    f"described as {fmt}, recorded in its Wikipedia infobox and "
                    f"asserted rather than measured.").strip()

            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'wikipedia', %s, %s, %s, %s, 'text/plain',
                           'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"],
                 f"https://en.wikipedia.org/wiki/{row['call_sign']}",
                 f"{row['call_sign']} — format", body, content_hash(body)),
            )

            cur.execute(
                """UPDATE lead
                      SET state = 'pending', attempts = 0, owner_agent = NULL,
                          lease_expires_at = NULL, lease_token = NULL,
                          next_action_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND target_hash = %s AND state != 'pending'""",
                (tenant_id, f"embed_party:{party_id}"),
            )

    looked = len(prepared["rows"])
    return fleet.Outcome(
        summary=(f"{prepared['prefix']}: {written} format(s) from {looked} station(s) "
                 f"without one"),
        calls=1, facts=written,
    )


#: Give a station a genre when Radio Browser could not, by reading the format out of its
#: Wikipedia infobox. Writes nothing when there is nothing to read.
enrich_genre = fleet.NetworkAgent(fetch=_fetch_enrich_genre,
                                  write=_write_enrich_genre)


# ------------------------------------------------------------ index_podcasts
#
# The second channel. `index_stations`, `index_streams` and `enrich_genre` between them
# build a radio index, and every row in it is a counterparty this label can describe and
# cannot yet address: the music director a pitch has to reach is a person the licence
# register does not name and the stream directories have never heard of. A podcast feed
# carries that person in `<itunes:owner>` and the show's own site in `<link>`, which is
# why `023_podcast_source.sql` argues a podcast belongs in the same table as a station
# rather than in a new one — it is another proof of migration 009's argument, not an
# exception to it.
#
# **One lead per block of `podcastindex.BLOCK` feed IDs**, and `podcastindex.py` sets out
# at length why the unit of work is an ID block rather than a category page. The short
# version is that ID blocks tile: block *k* is the half-open range `[k·BLOCK,
# (k+1)·BLOCK)`, every feed is in exactly one block, and a block is addressable and
# re-runnable without reference to any other. That is what makes a fifteen-thousand-lead
# sweep safe to interrupt, which the category endpoint's timestamp walk is not.
#
# **No `suggestion` step**, on the same test `index_stations` passes and for the same
# reason. `suggestion` exists for guesses about *identity*, and there is none here: a
# Podcast Index feed ID is a canonical key for "this show", so the show's existence and
# its owner are keyed facts rather than a name match. The one thing this stage infers,
# `show_kind`, is written `inferred` and never promoted.
#
# **The host does not become a party.** `ownerName` is a string, and turning a string
# into a person-party is the identity claim that produced thirteen junk parties from a
# name-match harvest in August. It is written as a fact on the show — the same place and
# shape as a station's `licensee` — and becomes a `contact_route.addressee` later, when
# there is a route to attach it to. That split is what migration 018 was written to make.
#
# This stage is the change `023` said would come: that migration shipped its
# `agent_manifest` row `enabled = false` because `agents.py` was owned by concurrent work,
# and named this flip as the trigger for deleting its own explanation.
# `027_enable_podcasts.sql` is the flip.

#: Confidence by provenance class, because provenance is the only thing that separates
#: these facts' strength. A per-dimension number would claim a precision this source
#: cannot support — Podcast Index does not say how sure it is of anything — and inventing
#: one per dimension would be the confident guess `SCOPE-RESET §2a` rule 1 is about.
#: `asserted` is 0.5 deliberately: the same number `index_streams` writes for a musical
#: station's tags, because `podcastindex.py` argues at length that a show's self-chosen
#: category is not *stronger* than a stranger's tag, only differently unreliable.
PODCAST_CONFIDENCE = {"measured": 0.9, "asserted": 0.5, "inferred": 0.3}


def _fetch_index_podcasts(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate) -> dict[str, Any]:
    """One block of Podcast Index feed IDs, narrowed to the shows worth writing.

    **This stage makes no paid call and the gate is never consulted.** Podcast Index is
    free and publishes no quota, which is what `023`'s `cost_class = 'free'` records;
    there is no `spend.estimate` key for it and asking for one would raise by design. The
    cost this stage *causes* is the embedding of every show it writes, and that is charged
    where it happens — on the `embed_party` leads returned below. Saying that here rather
    than leaving an unused parameter to be interpreted, because "no gate call" and "a gate
    call somebody forgot" look identical from outside the function.

    The pitchability filter runs in this half rather than the write half on purpose.
    `podcastindex.pitchable` is pure — it reads the feed record and nothing else — so
    running it before the transaction opens keeps the write phase to database statements,
    which is the entire reason `NetworkAgent` is two functions instead of one.

    **The adapter's two failures are translated rather than left to propagate**, and the
    translation is the point. `work_once`'s catch-all records `repr(exc)` and always fails
    the lead *transiently*, so an unconfigured deployment would burn four attempts against
    a missing key and land a `NotConfigured(...)` repr in the console where an operator is
    looking for a sentence. `Refused` already carries `permanent`, for precisely the
    distinction the fleet needs — a 429 belongs back on the frontier and a 401 does not —
    and forwarding it is the whole reason that attribute exists.
    """
    raw = str(lead.get("target") or "").strip()
    if not raw:
        raise fleet.LeadFailed(
            "index_podcasts needs a feed-ID block start in `target`", permanent=True)
    try:
        start = int(raw)
    except ValueError:
        raise fleet.LeadFailed(
            f"{raw!r} is not a feed-ID block start", permanent=True) from None
    if start < 0:
        raise fleet.LeadFailed(
            f"a feed-ID block starts at or after 0; got {start}", permanent=True)

    try:
        feeds = podcastindex.block(start)
    except podcastindex.NotConfigured as exc:
        raise fleet.LeadFailed(str(exc), permanent=True) from exc
    except podcastindex.Refused as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    return {"start": start, "seen": len(feeds),
            "feeds": [f for f in feeds if podcastindex.pitchable(f)]}


def _write_index_podcasts(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write each music show as a counterparty, with its provenance kept in three pieces.

    Shaped like `_write_index_stations`, because a podcast and a radio station are the
    same thing to this schema: a party with a role, some facts, a document that says who
    it is, and an embedding computed from that document.

    What is not the same is the provenance. A station's facts split two ways; a feed
    record splits three, and `podcastindex.py`'s header argues each boundary. Written
    here exactly as it argues them, because the classification only means anything if it
    survives the trip into the column that records it:

      * **`measured`** — `role` and `episode_count`. Podcast Index's crawler fetched the
        feed and counted what came back. That we did not personally make the measurement
        does not demote it, which is the same reasoning `026_role_backfill.sql` uses to
        call an FCC-sourced role `measured`.
      * **`asserted`** — `host`, `description`, `genre`, `language`. The publisher wrote
        these about themselves. Self-description is not more reliable than a stranger's
        tag; it is differently unreliable, and a show files itself under `Music` because
        that is where its listeners browse.
      * **`inferred`** — `show_kind`. This module's own reading of the weakest input in
        the file. Labelled, never promoted, and rendered by `profiles.compose` as a hedge.

    **Three of those dimension names are load-bearing and cannot be renamed here alone.**
    `profiles.from_facts` is the rebuild path — `ingest.recompose_profiles` runs it over
    every counterparty and replaces the text their vectors were built from — and it reads
    the composer's inputs back out of `party_fact` by dimension. It requires `role`,
    excises `host` from the prose so rule 2 reaches inside the publisher's own sentences,
    and fills the `prose` slot from `description`.

    So each of the three is written at ingest for the same reason, which is worth stating
    once rather than three times: a source module holds these in its hand and the rebuild
    path has only what was stored. Omit `role` and `from_facts` raises, the show is
    excluded from the rebuild's own selection, and it is counted and reported — loud, and
    recoverable. Omit `description` and nothing fails at all: the next
    `--recompose-profiles` rebuilds eighty-five thousand podcasts without the single best
    signal this source has, every vector quietly gets worse, and the only way anyone finds
    out is by reading two documents side by side. That is the failure `profiles.py` was
    written about, and writing the fact is what makes it impossible rather than unlikely.

    **A show already indexed is refreshed, not skipped.** `index_stations` can `continue`
    on a conflict because a licence record is a static row; a feed's episode count and
    categories change under it, and `--requeue index_podcasts` exists to re-read a block.
    Every statement below is `ON CONFLICT DO NOTHING` or hash-deduplicated, so a refresh
    that finds nothing new writes nothing and queues nothing.

    **`content_hash` covers the party id as well as the body**, unlike every writer here
    except `ingest.recompose_profiles`, and it has to. `profiles.compose` is terse by
    design, so two music shows with the same categories, the same language and no blurb
    compose to a byte-identical document; under `UNIQUE (tenant_id, content_hash)` the
    first would get its document and every one after it would silently get none, leaving
    them unembeddable. That is the defect that stranded 63 parties last time. The hash is
    a deduplication key, not a semantic identity, and "this party's profile, with this
    content" is what is being deduplicated.

    **`contact_route` is deliberately not written**, which `023` names as the point rather
    than a gap. Podcast Index does not publish the owner's email address, and this stage
    does not manufacture one from the show's domain — 018's header calls a pattern guess
    (`music@<domain>`) the fastest way to earn a spam complaint. The show's website lands
    in `presence`, which is a surface you may *read*; the route to send to is read off
    that page later, by a stage that can say where it got it.
    """
    tenant_id, start = lead["tenant_id"], prepared["start"]
    created = refreshed = documents = facts = 0
    follow_on: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        for feed in prepared["feeds"]:
            feed_id = feed["id"]
            title = str(feed.get("title") or "").strip()
            if not title:
                # A show with no name is a row nobody can read on a shortlist, and the
                # feed ID is not a name. Skipped rather than titled from its URL, which
                # would be a fabrication with a plausible shape.
                continue

            # The feed ID is Podcast Index's own key for "this show" and is never
            # reused, so it is what the slug is built on — the same role `facility_id`
            # plays for a station. A show that renames itself keeps its row.
            slug = f"pi-{feed_id}"[:120]
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                      contact_state, status)
                   VALUES (%s, %s, %s, 'organisation', 'counterparty', 'contactable',
                           'active')
                   ON CONFLICT (tenant_id, slug) DO NOTHING
                RETURNING id""",
                (tenant_id, slug, title[:200]),
            )
            fresh = cur.fetchone()
            if fresh is not None:
                party_id = fresh["id"]
                created += 1
                # `kind = 'organisation'` rather than `'person'`, for the reason
                # `index_stations` gives: the party is the show, and the human who
                # presents it is a fact on it and later an addressee on a route.
                cur.execute(
                    """INSERT INTO party_role (tenant_id, party_id, role)
                       VALUES (%s, %s, 'podcast_programmer') ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id),
                )
            else:
                cur.execute(
                    "SELECT id FROM party WHERE tenant_id = %s AND slug = %s",
                    (tenant_id, slug),
                )
                existing = cur.fetchone()
                if existing is None:
                    # The INSERT conflicted, so a row with this slug exists, and the
                    # SELECT is in the same transaction and therefore the same snapshot.
                    # Reaching here means those two statements disagree about the same
                    # table. There is no sensible value to continue with, and continuing
                    # would drop this show silently on every future run too.
                    raise fleet.LeadFailed(
                        f"party slug {slug!r} conflicted on INSERT but could not then "
                        "be read back in the same transaction — the block is abandoned "
                        "rather than written half-way", permanent=False)
                party_id = existing["id"]
                refreshed += 1

            # Three keys, all `measured`: the index assigned the first, the feed
            # publishes the second under the podcast namespace, and Apple assigned the
            # third. `party_identifier` is unique on `(tenant_id, kind, value)`, so these
            # are what a later podcast source joins on instead of parsing a display name.
            keys = [("podcastindex_feed_id", str(feed_id))]
            guid = str(feed.get("podcastGuid") or "").strip()
            if guid:
                keys.append(("podcast_guid", guid))
            itunes = feed.get("itunesId")
            if itunes:
                keys.append(("itunes_id", str(itunes)))
            for key_kind, value in keys:
                cur.execute(
                    """INSERT INTO party_identifier (tenant_id, party_id, kind, value,
                                                     provenance)
                       VALUES (%s, %s, %s, %s, 'measured')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id, key_kind, value[:200]),
                )

            cats = podcastindex.categories(feed)
            # Genre as one row rather than one per category: `fact_one_live_per_dimension`
            # allows a single live fact per (dimension, provenance), and a show's
            # categories are one claim about it, not four independent ones.
            claims = [
                ("role", "podcast", "measured", None),
                ("episode_count", str(int(feed.get("episodeCount") or 0)),
                 "measured", None),
                ("host", podcastindex.host(feed), "asserted", None),
                ("description", podcastindex.blurb(feed), "asserted", None),
                ("genre", ", ".join(cats), "asserted", Json(cats) if cats else None),
                ("language", str(feed.get("language") or "").strip(), "asserted", None),
                ("show_kind", podcastindex.show_kind(cats), "inferred", None),
            ]
            for dimension, value, provenance, structured in claims:
                # An empty value is not written. A show whose feed names no owner gets no
                # `host` fact, not a blank one — the absence is the honest answer and
                # `enrich_genre` writes nothing on the same principle.
                if not value:
                    continue
                cur.execute(
                    """INSERT INTO party_fact (tenant_id, party_id, dimension, value_text,
                                               value_json, provenance, status, confidence,
                                               source, written_by)
                       VALUES (%s, %s, %s, %s, %s, %s, 'live', %s, 'podcast_index',
                               'index_podcasts')
                       ON CONFLICT DO NOTHING""",
                    # Not truncated here, deliberately. Every value above is already
                    # bounded by the adapter — `host` at 200 characters, `description` at
                    # `podcastindex.BLURB_CHARS` — and a second, tighter cap applied at
                    # the last moment would silently shorten the one field the rebuild
                    # path reads back, in a place nobody would think to look for it.
                    (tenant_id, party_id, dimension, value, structured, provenance,
                     PODCAST_CONFIDENCE[provenance]),
                )
                facts += cur.rowcount

            # The show's own site, as a `presence` and not a `contact_route`. See the
            # docstring: a homepage is a surface you can read, and conflating that with an
            # endpoint you may send to is how a crawler's URL ends up in a mail merge.
            site = str(feed.get("link") or "").strip()
            if site:
                cur.execute(
                    """INSERT INTO presence (tenant_id, subject_kind, subject_id,
                                             platform, mode, handle, url, state,
                                             match_basis, confidence)
                       VALUES (%s, 'party', %s, 'web', 'owned', %s, %s, 'present',
                               'asserted', 0.6)
                       ON CONFLICT (tenant_id, subject_kind, subject_id, platform)
                       DO NOTHING""",
                    (tenant_id, party_id, title[:120], site[:500]),
                )

            # The document is the evidence the embedding is computed from. A vector whose
            # source text has been thrown away cannot be argued with when a shortlist
            # looks wrong, which is why every counterparty writer here keeps one.
            #
            # `lang` is `'en'` and that is about *this* text, not about the show: the body
            # is English prose `profiles.compose` wrote. What language the show is in is
            # the `language` fact above, where it can be read as a claim about the show.
            body = podcastindex.profile_text(feed)
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'podcast_index', %s, %s, %s, %s, 'text/plain',
                           'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"], site[:500],
                 f"{title[:120]} — Podcast Index feed", body,
                 content_hash(f"{party_id}:{body}")),
            )
            if cur.rowcount == 0:
                # This exact text is already on this party, so the vector computed from
                # it is current and re-queueing the embedding would be paying twice for
                # the same answer. This is what makes a re-run of a block cheap.
                continue
            documents += 1

            # The show has a document nothing has embedded yet. An already-completed
            # `embed_party` lead is *requeued* rather than re-inserted, because
            # `(tenant_id, target_hash)` is unique and a second insert would silently do
            # nothing — leaving the show embedded on text that predates this document.
            cur.execute(
                """UPDATE lead
                      SET state = 'pending', attempts = 0, owner_agent = NULL,
                          lease_expires_at = NULL, lease_token = NULL,
                          next_action_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND target_hash = %s AND state != 'pending'""",
                (tenant_id, f"embed_party:{party_id}"),
            )
            if cur.rowcount == 0:
                follow_on.append({
                    "kind": "embed_party", "adapter": "podcast_index",
                    "platform": "podcast_index", "party_id": str(party_id),
                    "scope_kind": "party", "target": str(party_id),
                    "target_hash": f"embed_party:{party_id}",
                    "reason": f"{title[:60]} indexed from Podcast Index, not yet "
                              "searchable",
                    "score": 0.6,
                })

    return fleet.Outcome(
        summary=(f"feeds {start}–{start + podcastindex.BLOCK}: {created} new show(s), "
                 f"{refreshed} refreshed, from {len(prepared['feeds'])} music feed(s) "
                 f"of {prepared['seen']} in the block"),
        calls=1, facts=facts, documents=documents, leads=len(follow_on),
        follow_on=follow_on,
    )


#: Build the standing podcast index, one block of Podcast Index feed IDs at a time, and
#: queue each show for embedding so it is shortlistable the day a record is ready.
index_podcasts = fleet.NetworkAgent(fetch=_fetch_index_podcasts,
                                    write=_write_index_podcasts)


#: Which agent handles which lead kind. The fleet reads this; no agent reads it.
REGISTRY: dict[str, fleet.Agent | fleet.NetworkAgent] = {
    "embed_document": embedder,
    "map_source": map_source,
    "find_counterparties": find_counterparties,
    "profile_party": profile_party,
    "embed_party": embed_party,
    "distil_lesson": distil_lesson,
    "analyse_recording": analyse_recording,
    "index_stations": index_stations,
    "index_streams": index_streams,
    "enrich_genre": enrich_genre,
    "index_podcasts": index_podcasts,
    # These three live in their own modules rather than in this file — `streams.py` and
    # `contacts.py` — so the only thing they need from here is a line in the registry. The
    # fleet reads this mapping and nothing else; an agent is a pair of callables and is
    # not obliged to be defined next to its neighbours. Keeping them out also keeps this
    # file, which several workstreams touch at once, down to a four-line diff.
    "refresh_stream": streams.refresh_stream,
    "harvest_contacts": contacts.harvest_contacts,
    "harvest_feed_contacts": contacts.harvest_feed_contacts,
}
