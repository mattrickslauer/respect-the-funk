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

from rtf_platform import embed, fleet, harvested, lessons, repo, sources, spend

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
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tenant_id, party_id, title, body FROM party_document WHERE id = %s",
            (document_id,),
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
        cur.execute("DELETE FROM party_chunk WHERE document_id = %s", (doc["id"],))
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
                         WHERE d.id = m.document_id) AS title,
                       (SELECT d.url FROM party_document d
                         WHERE d.id = m.document_id) AS url
                  FROM matched m
                 ORDER BY m.distance""",
            (vectors[0].literal(), tenant_id, provider.model, vectors[0].literal(), limit),
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
            # history intact, which is what `supersedes_id` is for.
            cur.execute(
                """UPDATE party_fact SET status = 'superseded'
                    WHERE tenant_id = %s AND party_id = %s AND dimension = %s
                      AND provenance = %s AND status = 'live' AND value_text != %s""",
                (tenant_id, party_id, fact.dimension,
                 fact.provenance, fact.value_text),
            )
            cur.execute(
                """INSERT INTO party_fact
                        (tenant_id, party_id, dimension, value_text, provenance,
                         confidence, source, written_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (tenant_id, party_id, fact.dimension, fact.value_text,
                 fact.provenance, fact.confidence, platform, "map_source"),
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
        cur.execute("SELECT name FROM party WHERE id = %s", (party_id,))
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
        cur.execute(
            """SELECT DISTINCT value_text FROM party_fact
                WHERE tenant_id = %s AND party_id = %s AND dimension = 'genre'
                  AND status = 'live' LIMIT 5""",
            (tenant_id, party_id),
        )
        terms = [r["value_text"] for r in cur.fetchall()]
    cap = int(budget["max_leads_per_run"]) if budget else 25

    try:
        harvest = adapter.discover_counterparties(
            artist_name, ident["value"] if ident else "", terms=terms)
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
            cur.execute(
                """INSERT INTO presence (tenant_id, subject_kind, subject_id, platform,
                                         mode, handle, url, state, match_basis,
                                         confidence)
                   VALUES (%s, 'party', %s, %s, 'observed', %s, %s, 'present',
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
        cur.execute("SELECT name, artist_type, kind FROM party WHERE id = %s", (party_id,))
        party = cur.fetchone()
        if party is None:
            raise fleet.LeadFailed("the party is gone", permanent=True)

        cur.execute(
            """SELECT r.title, r.isrc FROM recording r
                 JOIN party_credit c ON c.subject_id = r.id AND c.subject_kind = 'recording'
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
                             WHERE pr.subject_kind = 'party' AND pr.subject_id = s.id
                             ORDER BY pr.url LIMIT 1) AS url
                      FROM shortlisted s
                     ORDER BY s.distance""",
                (artist["vec"], tenant_id, artist["embedding_model"], artist["vec"],
                 SHORTLIST_CANDIDATES),
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
                    "UPDATE lesson SET hit_count = hit_count + 1 WHERE id = ANY(%s)",
                    (list(spent),),
                )

    return ranked


#: Which agent handles which lead kind. The fleet reads this; no agent reads it.
REGISTRY: dict[str, fleet.Agent | fleet.NetworkAgent] = {
    "embed_document": embedder,
    "map_source": map_source,
    "find_counterparties": find_counterparties,
    "profile_party": profile_party,
    "embed_party": embed_party,
}
