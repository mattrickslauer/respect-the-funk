"""Load documents into the spine, then let the fleet embed them.

    python -m rtf_platform.ingest ../../docs/research/*.md
    python -m rtf_platform.ingest --drain-only
    python -m rtf_platform.ingest --search "what did we learn about creator rates"

This deliberately does **not** embed anything itself. It writes `party_document` rows and
one `embed_document` lead per document, and then runs the fleet loop. The embedding
happens because a row appeared and a worker claimed it — which is the architecture's
whole claim, and it is worth more as a demonstrated path than as a paragraph.

Killing this process midway is safe and is the point: the documents are committed, the
leads stay `pending`, and the next run picks up exactly what is left. Nothing tracks
progress except the rows.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from rtf_platform import (
    agents, fcc, fleet, mail, outreach, podcastindex, profiles, radiobrowser, sender,
    spend, streams,
)


def _tenant(conn: psycopg.Connection, slug: str | None) -> str:
    with conn.cursor() as cur:
        if slug:
            cur.execute("SELECT id FROM tenant WHERE slug = %s", (slug,))
        else:
            cur.execute("SELECT id FROM tenant ORDER BY created_at LIMIT 2")
        rows = cur.fetchall()
    if not rows:
        sys.exit("no tenant exists — create one through the console first")
    if len(rows) > 1:
        sys.exit("more than one tenant; pass --tenant <slug> to choose")
    return str(rows[0]["id"])


def load(conn: psycopg.Connection, tenant_id: str, paths: list[Path]) -> tuple[int, int]:
    """Write a document and a lead for each path. Returns (documents, leads).

    Both writes happen in one transaction per document, so a lead never exists without
    the document it names — the failure that would otherwise show up much later as an
    agent parking a lead for a row that was never committed.
    """
    documents = leads = 0
    for path in paths:
        body = path.read_text(errors="replace").strip()
        if not body:
            print(f"  skip   {path.name} (empty)")
            continue

        digest = agents.content_hash(body)
        title = path.stem.replace("-", " ")
        with conn.transaction():
            with conn.cursor() as cur:
                # Asked before writing rather than inferred from the write. Postgres
                # would expose `xmax = 0` to tell an insert from an update in one
                # statement; CockroachDB has no such system column, and a lookup inside
                # the same transaction is exact anyway.
                cur.execute(
                    "SELECT id FROM party_document WHERE tenant_id = %s AND content_hash = %s",
                    (tenant_id, digest),
                )
                existing = cur.fetchone()

                cur.execute(
                    """INSERT INTO party_document (tenant_id, platform, url, title, body,
                                                   content_hash, mime, lang, http_status)
                       VALUES (%s, 'internal', %s, %s, %s, %s, 'text/markdown', 'en', 200)
                       ON CONFLICT (tenant_id, content_hash) DO UPDATE
                          SET fetched_at = now()
                    RETURNING id""",
                    (tenant_id, f"repo://{path.as_posix()}", title, body, digest),
                )
                document_id = str(cur.fetchone()["id"])
                if existing is None:
                    documents += 1

                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                         target, target_hash, platform, reason, score)
                       VALUES (%s, 'tenant', 'embed_document', 'auto', 'internal',
                               %s, %s, 'internal', 'document ingested, needs embedding', 0.8)
                       ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                    (tenant_id, document_id, f"embed:{digest}"),
                )
                if cur.rowcount:
                    leads += 1
        print(f"  loaded {path.name}  ({len(body):,} chars)")
    return documents, leads


def drain(conn: psycopg.Connection, tenant_id: str, *, worker_name: str,
          kinds: list[str] | None = None) -> int:
    """Run the fleet until the frontier is empty, or until it stops making progress.

    Every kind in the registry gets a pass per round, and the rounds repeat while
    anything is still being worked. That matters because agents write leads for other
    agents: mapping a source produces `map_release` leads, and draining has to keep going
    until the expansion actually settles rather than stopping after one sweep.
    """
    kinds = kinds or list(agents.REGISTRY)
    total = 0
    with fleet.worker(conn, tenant_id, worker_name):
        while True:
            round_total = 0
            for kind in kinds:
                agent = agents.REGISTRY.get(kind)
                if agent is None:
                    continue
                worked = fleet.work_once(conn, tenant_id, worker_name, agent,
                                         kinds=[kind])
                if worked:
                    print(f"  {kind}: worked {worked}")
                round_total += worked
            if not round_total:
                break
            total += round_total
    return total


def seed_sources(conn: psycopg.Connection, tenant_id: str) -> int:
    """Write a `map_source` lead for every presence row that has never had one.

    A backfill, needed exactly once: the two Spotify surfaces on the roster were asserted
    before adding a source queued the work of reading it. New ones are seeded by
    `repo.upsert_presence` in the same transaction as the presence row, so this stops
    being necessary the moment it has run.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score,
                                 cadence_seconds)
               SELECT p.tenant_id, 'party', p.subject_id, 'map_source', 'auto',
                      p.platform, coalesce(nullif(p.url, ''), p.handle),
                      'map:' || p.platform || ':' || p.subject_id::STRING, p.platform,
                      'source asserted by an operator, never mapped', 0.9,
                      m.cadence_seconds
                 FROM presence p
                 LEFT JOIN source_manifest m ON m.platform = p.platform
                WHERE p.tenant_id = %s AND p.subject_kind = 'party' AND p.enabled
                  AND coalesce(nullif(p.url, ''), p.handle) != ''
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id,),
        )
        return cur.rowcount


def discover_sources(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue a name search on every enabled keyless source, for every party.

    This is the other half of what an operator means by "populate what you can". Adding
    a *source* says where to look; adding an *artist* should start the looking, against
    whatever we can reach without asking anyone for a credential.

    Only sources that accept a name are queued, and only ones already enabled — so this
    widens automatically as adapters land and sources are switched on, without this
    function learning their names. The manifest is the list; this is not a second copy
    of it.

    A search is inference, so nothing it finds becomes a presence row. It becomes a
    `suggestion`, and a human accepts it. `map:` and `find:` are separate target_hash
    prefixes precisely so a discovered surface and an asserted one never collide.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score,
                                 cadence_seconds)
               SELECT %s, 'party', p.id, 'map_source', 'auto', m.platform,
                      p.name, 'find:' || m.platform || ':' || p.id::STRING, m.platform,
                      'artist on the roster, ' || m.platform || ' never searched', 0.5,
                      m.cadence_seconds
                 FROM party p
                 CROSS JOIN source_manifest m
                WHERE p.tenant_id = %s AND p.status = 'active'
                  AND m.enabled AND m.auth = 'none'
                  AND NOT EXISTS (
                      SELECT 1 FROM presence pr
                       WHERE pr.tenant_id = p.tenant_id AND pr.subject_kind = 'party'
                         AND pr.subject_id = p.id AND pr.platform = m.platform)
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id, tenant_id),
        )
        return cur.rowcount


def prospect(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue profiling and counterparty discovery for every roster artist.

    Only the roster: discovering counterparties *for* a counterparty is how a frontier
    turns into a crawl of the whole of Deezer, and `lead.depth` bounding it after the fact
    is a worse answer than not starting.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score)
               SELECT %s, 'party', p.id, k.kind, 'auto', 'deezer', p.id::STRING,
                      k.kind || ':' || p.id::STRING, 'deezer', k.reason, 0.8
                 FROM party p
                 CROSS JOIN (VALUES
                     ('profile_party', 'roster artist, no profile composed yet'),
                     ('find_counterparties', 'roster artist, nobody shortlisted yet')
                 ) AS k(kind, reason)
                WHERE p.tenant_id = %s AND p.status = 'active'
                  AND p.party_class = 'roster'
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id, tenant_id),
        )
        return cur.rowcount


def index_stations(conn: psycopg.Connection, tenant_id: str,
                   states: list[str] | None = None, band: str = "nce") -> int:
    """Queue one `index_stations` lead per US jurisdiction.

    Unlike `prospect`, these leads are **tenant-scoped**: the radio index belongs to the
    label, not to any one artist. That is the point of building it up front — when a
    single is ready the stations are already there, already embedded, and the shortlist
    is a query rather than a discovery run. `lead_scope_shape` requires `party_id IS NULL`
    for a tenant-scoped lead, so none is passed.

    `target_hash` is `index_stations:<state>`, so seeding twice is a no-op rather than
    fifty-one duplicate fetches. The register does change, and re-reading it is what
    `--requeue index_stations` is for — an explicit hand-crank, the same shape as
    `--reanalyse`, rather than a silent re-fetch nobody asked for.
    """
    if band not in fcc.BANDS:
        raise ValueError(f"{band!r} is not a band; known: {', '.join(sorted(fcc.BANDS))}")

    wanted = states or fcc.JURISDICTIONS
    queued = 0
    with conn.cursor() as cur:
        for state in wanted:
            # The NCE band keeps the bare `<STATE>` target it was first seeded with, so
            # re-seeding does not duplicate the 56 leads that already ran. Every other
            # band carries its name in both the target and the hash.
            target = state if band == "nce" else f"{state}:{band}"
            cur.execute(
                """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                     target, target_hash, platform, reason, score)
                   VALUES (%s, 'tenant', 'index_stations', 'auto', 'fcc', %s, %s,
                           'fcc', %s, 0.6)
                   ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                (tenant_id, target, f"index_stations:{target}",
                 f"{state} {band} licences not yet in the counterparty index"),
            )
            queued += cur.rowcount
    return queued


def index_streams(conn: psycopg.Connection, tenant_id: str,
                  countries: list[str] | None = None) -> int:
    """Queue `index_streams` leads, one per page of each country's stations.

    The page count is derived from Radio Browser's own `/json/countries` counts rather
    than guessed, so a country that grows between runs gets the leads its size warrants
    instead of silently losing its tail to a hardcoded number. Re-seeding is a no-op on
    the pages that already exist and adds only the new ones.
    """
    counts = {c["iso_3166_1"].upper(): int(c.get("stationcount") or 0)
              for c in radiobrowser.countries() if c.get("iso_3166_1")}
    wanted = [c.strip().upper() for c in (countries or ["US"]) if c.strip()]

    queued = 0
    with conn.cursor() as cur:
        for country in wanted:
            total = counts.get(country, 0)
            if total == 0:
                continue
            for offset in range(0, total, radiobrowser.PAGE):
                target = f"{country}:{offset}"
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                         target, target_hash, platform, reason, score)
                       VALUES (%s, 'tenant', 'index_streams', 'auto', 'radio_browser',
                               %s, %s, 'radio_browser', %s, 0.6)
                       ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                    (tenant_id, target, f"index_streams:{target}",
                     f"{country} stations {offset}–{offset + radiobrowser.PAGE} have "
                     "no genre or website yet"),
                )
                queued += cur.rowcount
    return queued


def feed_id_range(text: str) -> tuple[int, int | None]:
    """`"0-20000"` into `(0, 20000)`; `""` into `(0, None)`, meaning the whole index.

    Anything else raises. There is no lenient reading of `--index-podcasts 0..20000` or
    `--index-podcasts 20000` on offer, because every lenient reading of a *range* is a
    guess about which end the operator meant, and guessing wrong here seeds thousands of
    leads against the wrong part of a four-million-feed index. A typo should cost one
    error message.
    """
    raw = text.strip()
    if not raw:
        return 0, None
    first, sep, last = raw.partition("-")
    if not sep:
        raise ValueError(
            f"{text!r} is not a feed-ID range. Give it as FIRST-LAST, e.g. 0-20000, "
            "or pass --index-podcasts with no value to cover the whole index.")
    try:
        start, ceiling = int(first.strip()), int(last.strip())
    except ValueError:
        raise ValueError(
            f"{text!r} is not a feed-ID range: both ends must be whole numbers, "
            "e.g. 0-20000") from None
    if start < 0 or ceiling < start:
        raise ValueError(
            f"{text!r} does not run upwards from zero; a feed-ID range is "
            "FIRST-LAST with 0 <= FIRST <= LAST")
    return start, ceiling


def index_podcasts(conn: psycopg.Connection, tenant_id: str, *, start: int = 0,
                   ceiling: int | None = None) -> int:
    """Queue `index_podcasts` leads, one per block of Podcast Index feed IDs.

    Tenant-scoped, like `index_stations` and `index_streams` and for the same reason: the
    podcast index belongs to the label rather than to any one artist, and the value of
    building it up front is that when a single is ready the shows that might play it are
    already embedded and the shortlist is a query. `lead_scope_shape` requires
    `party_id IS NULL` for a tenant-scoped lead, so none is passed.

    **The ceiling is read from the source, not chosen.** `podcastindex.newest_feed_id()`
    asks the index how far it currently goes, exactly as this module's `index_streams`
    asks Radio Browser for its country counts. A hardcoded ceiling would quietly stop
    covering the tail of the index the week after it was written, and quietly is the
    problem — nothing would report an error, there would simply be fewer podcasts.

    **Credentials are demanded before the first INSERT, not at the first fetch.** Podcast
    Index cannot be read at all without a key, so seeding fifteen thousand leads that
    every worker will fail is worse than refusing: it fills the frontier with work nobody
    can do and buries the one sentence that says why. `podcastindex.credentials()` raises
    `NotConfigured` with the signup URL and the two variable names, which is the whole of
    what an operator needs. This is called even when `ceiling` is supplied by hand, so
    there is no path that seeds a frontier an unconfigured deployment cannot work.

    `target_hash` is `index_podcasts:<block start>`, so re-seeding is a no-op on blocks
    that already exist and adds only the ones the index has grown since. Re-reading a
    block that has already run will be `--requeue index_podcasts` — the same explicit
    hand-crank as `--reanalyse`, rather than a silent re-fetch nobody asked for.

    **What this does not yet do is work the leads it writes.** `agents.REGISTRY` has no
    `index_podcasts` entry, because `agents.py` is owned by concurrent work; migration
    023 declares the stage `enabled = false` and explains the split at length. Until that
    entry lands, a seeded lead sits `pending` — visible in the frontier and in
    `research.fleet`'s "declared, not running" branch, which is an accurate report of the
    state rather than a stage that appears to run and writes nothing. That is also why
    `--requeue index_podcasts` exits today: it validates the kind against the registry.
    """
    podcastindex.credentials()
    if ceiling is None:
        ceiling = podcastindex.newest_feed_id()
    if start > ceiling:
        raise ValueError(
            f"a block range runs upwards: start={start} is past ceiling={ceiling}")

    queued = 0
    with conn.cursor() as cur:
        for first in podcastindex.blocks_to(ceiling, start=start):
            target = str(first)
            cur.execute(
                """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                     target, target_hash, platform, reason, score)
                   VALUES (%s, 'tenant', 'index_podcasts', 'auto', 'podcast_index',
                           %s, %s, 'podcast_index', %s, 0.6)
                   ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                (tenant_id, target, f"index_podcasts:{target}",
                 f"podcast feeds {first}–{first + podcastindex.BLOCK} have never been "
                 "read for music shows"),
            )
            queued += cur.rowcount
    return queued


def refresh_streams(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue one `refresh_stream` lead per UUID bucket that still has stations in it.

    Sixteen buckets, keyed on the first hex character of the Radio Browser station UUID,
    and only the ones with work — read from the index rather than enumerated, exactly as
    `enrich_genre` reads its call-sign prefixes, so this queues no empty leads and a bucket
    that finishes stops being seeded.

    `031_stream_refresh.sql` argues what the stage is for. The short version is that
    `index_streams` stored the tags and the homepage from Radio Browser's payload and let
    `countrycode`, `state` and `language` go, and the first of those is what
    `contacts.country_for` needs before a contact route may be written at all.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT left(i.value, 1) AS bucket
                 FROM party_identifier i
                 JOIN party p ON p.tenant_id = i.tenant_id AND p.id = i.party_id
                WHERE i.tenant_id = %s AND i.kind = 'radiobrowser_uuid'
                  AND p.party_class = 'counterparty'
                  AND NOT EXISTS (SELECT 1 FROM party_fact f
                                   WHERE f.tenant_id = i.tenant_id
                                     AND f.party_id = i.party_id
                                     AND f.dimension = 'country_code')
                ORDER BY 1""",
            (tenant_id,),
        )
        buckets = [r["bucket"] for r in cur.fetchall() if r["bucket"] in streams.BUCKETS]

        queued = 0
        for bucket in buckets:
            cur.execute(
                """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                     target, target_hash, platform, reason, score)
                   VALUES (%s, 'tenant', 'refresh_stream', 'auto', 'radio_browser', %s,
                           %s, 'radio_browser', %s, 0.6)
                   ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                (tenant_id, bucket, f"refresh_stream:{bucket}",
                 f"stations with UUIDs starting {bucket} have no country, market or "
                 "language recorded"),
            )
            queued += cur.rowcount
    return queued


def harvest_contacts(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue one `harvest_contacts` lead per counterparty this stage can actually work.

    Two predicates, and the second is the one worth explaining.

    **A `web` presence**, because that is the only page this stage is allowed to read —
    the counterparty's own site, which `index_streams` wrote as a presence precisely
    because a homepage is a surface to read and not an endpoint to send to.

    **Country evidence already on the row.** A party with neither an `fcc_facility_id`
    identifier nor a live `country_code` fact would be claimed by a worker,
    `contacts.country_for` would raise `LeadFailed(permanent=True)`, and the lead would
    park. Seeding those is the failure `index_podcasts` refuses when it demands
    credentials before its first INSERT: a frontier full of leads nobody can work buries
    the one sentence saying why. Measured on 2026-08-13 it is not a rounding error —
    6,314 counterparties have a homepage and only 872 have country evidence — so seeding
    blind would park 5,442 leads. `--refresh-streams` is what turns the rest into work,
    and re-running this afterwards picks them up.

    Idempotent on `(tenant_id, target_hash)` like every other producer here, so running it
    twice queues nothing the second time and running it after a refresh queues only what
    the refresh unblocked.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score)
               SELECT p.tenant_id, 'party', p.id, 'harvest_contacts', 'auto',
                      'station_website', p.id::STRING,
                      'harvest_contacts:' || p.id::STRING, 'web',
                      'counterparty publishes a website and has no contact route', 0.7
                 FROM party p
                WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
                  AND p.status = 'active'
                  AND EXISTS (SELECT 1 FROM presence pr
                               WHERE pr.tenant_id = p.tenant_id
                                 AND pr.subject_kind = 'party' AND pr.subject_id = p.id
                                 AND pr.platform = 'web' AND pr.state = 'present'
                                 AND pr.url != '')
                  AND (EXISTS (SELECT 1 FROM party_identifier i
                                WHERE i.tenant_id = p.tenant_id AND i.party_id = p.id
                                  AND i.kind = 'fcc_facility_id')
                       OR EXISTS (SELECT 1 FROM party_fact f
                                   WHERE f.tenant_id = p.tenant_id AND f.party_id = p.id
                                     AND f.dimension = 'country_code'
                                     AND f.status = 'live'))
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id,),
        )
        return cur.rowcount


def harvest_feed_contacts(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue one `harvest_feed_contacts` lead per indexed podcast show.

    The selection is "carries a `podcastindex_feed_id`", which is the identifier
    `index_podcasts` writes for every show it creates and the only thing this stage needs.
    No country predicate, unlike `harvest_contacts`: a feed cannot say where its owner is
    established, the route is written with `contact_country` NULL, and
    `032_feed_contact_harvest.sql` explains why that is honest and what it blocks.

    **Expect zero today.** The live cluster holds no podcast counterparties — `023` and
    `027` are unapplied and `index_podcasts` has never run — so this returns 0 until that
    stage has produced rows. That is reported by the caller rather than hidden.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score)
               SELECT p.tenant_id, 'party', p.id, 'harvest_feed_contacts', 'auto',
                      'podcast_index', p.id::STRING,
                      'harvest_feed_contacts:' || p.id::STRING, 'podcast_index',
                      'podcast show indexed, its feed may declare an owner address', 0.8
                 FROM party p
                WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
                  AND p.status = 'active'
                  AND EXISTS (SELECT 1 FROM party_identifier i
                               WHERE i.tenant_id = p.tenant_id AND i.party_id = p.id
                                 AND i.kind = 'podcastindex_feed_id')
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id,),
        )
        return cur.rowcount


def enrich_genre(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue one `enrich_genre` lead per two-letter call-sign prefix that needs one.

    The prefixes are read from the index rather than enumerated as K/W times the
    alphabet, so this queues exactly the buckets that exist and no empty ones. A prefix
    whose stations all already have a genre is skipped for the same reason.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT left(pi.value, 2) AS prefix
                 FROM party_identifier pi
                WHERE pi.tenant_id = %s AND pi.kind = 'call_sign'
                  AND NOT EXISTS (SELECT 1 FROM party_fact f
                                   WHERE f.tenant_id = pi.tenant_id
                                     AND f.party_id = pi.party_id
                                     AND f.dimension = 'genre')
                ORDER BY 1""",
            (tenant_id,),
        )
        prefixes = [r["prefix"] for r in cur.fetchall() if len(r["prefix"]) == 2]

        queued = 0
        for prefix in prefixes:
            cur.execute(
                """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                     target, target_hash, platform, reason, score)
                   VALUES (%s, 'tenant', 'enrich_genre', 'auto', 'wikipedia', %s, %s,
                           'wikipedia', %s, 0.7)
                   ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                (tenant_id, prefix, f"enrich_genre:{prefix}",
                 f"stations starting {prefix} have no genre yet"),
            )
            queued += cur.rowcount
    return queued


def recompose_profiles(conn: psycopg.Connection, tenant_id: str,
                       batch: int = 400) -> int:
    """Rewrite every counterparty's embedded profile text with `profiles.from_facts`.

    A one-off backfill, in the same spirit as `seed_sources` and `--retry-failed`: the
    composition rules changed, and 14,000 documents written under the old ones cannot fix
    themselves. `profiles.py` explains what was wrong with them — roughly half of each
    document was a string every other document also contained.

    Resumable by construction. The selection is "counterparties with no `profile_v2`
    document", so killing this mid-run loses nothing and re-running continues; there is no
    cursor to lose. Every rewritten party has its `embed_party` lead returned to the
    frontier, because a document nobody re-embeds is a document that never reaches R1.

    **`content_hash` is computed over `party_id` *and* the body**, unlike every other
    writer in this codebase. That is required rather than stylistic: the new composition
    is intentionally terse, so thousands of stations with no documented programming
    compose to the identical sentence, and a hash of the body alone would collide under
    `(tenant_id, content_hash)`. The first party would get its document and every
    subsequent one would silently get none — which is exactly the defect that stranded 63
    parties as unembeddable last time. The hash is a deduplication key, not a semantic
    identity, and "this party's profile, with this content" is the thing being deduped.
    """
    # Counted once, up front, because it cannot change while this runs and because the
    # operator wants the number before the pass rather than inferred from a shortfall
    # afterwards. `026_role_backfill.sql` explains who these are: parties whose source
    # never said what kind of thing they were, mostly rows carrying a name and nothing
    # else.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM party p
                WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
                  AND NOT EXISTS (SELECT 1 FROM party_fact r
                                   WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id
                                     AND r.dimension = 'role' AND r.status = 'live'
                                     AND trim(r.value_text) != '')""",
            (tenant_id,),
        )
        unrebuildable = cur.fetchone()["n"]
    if unrebuildable:
        print(f"  {unrebuildable} counterparties have no `role` fact and will be left "
              f"alone — see 026_role_backfill.sql")

    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.id, p.name FROM party p
                    WHERE p.tenant_id = %s AND p.party_class = 'counterparty'
                      AND NOT EXISTS (SELECT 1 FROM party_document d
                                       WHERE d.tenant_id = p.tenant_id
                                         AND d.party_id = p.id
                                         AND d.platform = 'profile_v2')
                      -- Excluded in the *selection* and not skipped in the loop, which
                      -- matters more than it looks: the selection is "has no profile_v2
                      -- document", so a party this pass declined to write would be
                      -- returned again on the next iteration, and the `while True` would
                      -- never see an empty batch. Filtering here terminates; filtering in
                      -- Python spins forever against the cluster.
                      AND EXISTS (SELECT 1 FROM party_fact r
                                   WHERE r.tenant_id = p.tenant_id AND r.party_id = p.id
                                     AND r.dimension = 'role' AND r.status = 'live'
                                     AND trim(r.value_text) != '')
                    LIMIT %s""",
                (tenant_id, batch),
            )
            rows = cur.fetchall()
            if not rows:
                return total

            ids = [r["id"] for r in rows]
            cur.execute(
                """SELECT party_id, dimension, value_text FROM party_fact
                    WHERE tenant_id = %s AND party_id = ANY(%s) AND status = 'live'""",
                (tenant_id, ids),
            )
            by_party: dict[Any, dict[str, str]] = {}
            for fact in cur.fetchall():
                by_party.setdefault(fact["party_id"], {})[fact["dimension"]] = (
                    fact["value_text"])

            for row in rows:
                # No `try` here on purpose. The selection above already guarantees a
                # `role` fact, so a raise from `from_facts` would mean that guarantee had
                # broken, and catching it would turn a contradiction between two queries
                # into a quietly smaller number.
                body = profiles.from_facts(row["name"], by_party.get(row["id"], {}))
                cur.execute(
                    """INSERT INTO party_document (tenant_id, party_id, platform, url,
                                                   title, body, content_hash, mime,
                                                   lang, http_status)
                       VALUES (%s, %s, 'profile_v2', '', %s, %s, %s, 'text/plain',
                               'en', 200)
                       ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                    (tenant_id, row["id"], f"{row['name']} — profile",
                     body, agents.content_hash(f"{row['id']}:{body}")),
                )
                cur.execute(
                    """UPDATE lead
                          SET state = 'pending', attempts = 0, owner_agent = NULL,
                              lease_expires_at = NULL, lease_token = NULL,
                              next_action_at = now(), updated_at = now()
                        WHERE tenant_id = %s AND target_hash = %s AND state != 'pending'""",
                    (tenant_id, f"embed_party:{row['id']}"),
                )
        total += len(rows)
        print(f"  recomposed {total}")


def send_self_test(conn: psycopg.Connection, tenant_id: str, address: str,
                   artist_slug: str = "hallow-youth") -> str:
    """Prepare one real pitch, addressed to ourselves, through the production path.

    Every step below is the same code an operator drives from the console — campaign,
    thread, draft, approve — and the same `outbox` row the Sender claims. Nothing is
    stubbed and no SQL is hand-written past the fixture party, which is the point: a
    smoke test that bypasses the gate proves the gate works right up until the day it
    does not.

    **The counterparty is named for what it is.** It is not a real station with a
    substituted address — putting a false `contact_route` against a real broadcaster
    would poison the system of record this project's whole argument rests on. It is a
    self-test target, labelled as one, whose route is `asserted` because a human really
    did type it.

    Leaves the row `pending` in the outbox. Delivery is `--send`, deliberately separate:
    preparing a send and performing one are different decisions and the second is the
    irreversible one.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM party WHERE tenant_id = %s AND slug = %s",
                    (tenant_id, artist_slug))
        artist = cur.fetchone()
        if artist is None:
            sys.exit(f"no roster artist with slug {artist_slug!r}")

        cur.execute(
            """INSERT INTO party (tenant_id, slug, name, kind, party_class,
                                  contact_state, status)
               VALUES (%s, 'self-test-delivery', 'Delivery self-test (not a station)',
                       'organisation', 'counterparty', 'contactable', 'active')
               ON CONFLICT (tenant_id, slug) DO UPDATE SET contact_state = 'contactable'
            RETURNING id""",
            (tenant_id,))
        target = cur.fetchone()["id"]

        cur.execute(
            """INSERT INTO contact_route (tenant_id, party_id, channel, value,
                                          addressee, provenance, state, confidence,
                                          source, written_by)
               VALUES (%s, %s, 'email', %s, 'self', 'asserted', 'verified', 1.0,
                       'operator', 'send_self_test')
               ON CONFLICT (tenant_id, party_id, channel, value) DO NOTHING""",
            (tenant_id, target, address))

    campaign = outreach.create_campaign(
        conn, tenant_id, party_id=str(artist["id"]),
        # `campaign.channel` is the *relationship*, not the transport — its CHECK
        # allows curator|ugc|press|radio|sync. The transport is `message.channel`,
        # which defaults to email. `distil_lesson` composes its sentence from this
        # one, so "a radio approach" is what a lesson will end up saying.
        name=f"Delivery self-test {address}", channel="radio",
        goal="prove the outbox, the sender and SES before anyone real is contacted")
    outreach.set_campaign_state(conn, tenant_id, str(campaign["id"]), "running")

    # `one_open_thread_per_counterparty` means a second run cannot open a second
    # conversation with the same party — the constraint fires as a `UniqueViolation`.
    # That is the behaviour the product wants and the smoke test must not fight it, so
    # an existing open thread is *reused* rather than closed and reopened. Closing it
    # would be worse than untidy: a terminal state queues `distil_lesson`, and the
    # lesson table would fill with "Delivery self-test declined a radio approach".
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, state FROM thread
                WHERE tenant_id = %s AND counterparty_id = %s
                  AND state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply')""",
            (tenant_id, target))
        open_already = cur.fetchone()

    if open_already is None:
        thread = outreach.open_thread(conn, tenant_id, campaign_id=str(campaign["id"]),
                                      counterparty_id=str(target))
    else:
        thread = open_already
        print(f"reusing the open thread ({thread['state']})")
    # The thread walks its own state machine rather than jumping: `discovered` may only
    # become `shortlisted`, and `shortlisted` only `approved`. `draft` picks it up from
    # there and walks `approved -> drafted -> awaiting_human` itself. Skipping an edge
    # here would be the smoke test proving a path the console cannot actually take.
    walk = ["discovered", "shortlisted", "approved"]
    state = thread.get("state", "discovered")
    if state in walk:
        for step in walk[walk.index(state) + 1:]:
            outreach.advance(conn, tenant_id, str(thread["id"]), step)
    elif state not in {"drafted", "awaiting_human", "queued"}:
        sys.exit(f"thread {thread['id']} is {state}; not a state this smoke test drives")

    message = outreach.draft(
        conn, tenant_id, str(thread["id"]),
        subject="Hallow Youth — Losing Sleep (dance, 124bpm)",
        body=("Hi,\n\n"
              "I run Respect the Funk. We have a new dance single from Hallow Youth "
              "called Losing Sleep and I think it fits what you programme.\n\n"
              "It is 124bpm, and I can send a WAV, a one-sheet and clearance details "
              "on request. If it is not right for you, telling me so is genuinely "
              "useful and I will not send you another.\n\n"
              "Thanks for listening,\n"
              "Respect the Funk"))
    outreach.approve(conn, tenant_id, str(thread["id"]), str(message["id"]),
                     approver="send_self_test")
    return str(thread["id"])


def show_shortlist(conn: psycopg.Connection, tenant_id: str, slug: str) -> int:
    """R1 at the command line: who should we take this artist to?"""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM party WHERE tenant_id = %s AND slug = %s",
                    (tenant_id, slug))
        artist = cur.fetchone()
    if artist is None:
        print(f"no artist with slug {slug!r}")
        return 1

    gate = spend.Gate.open(conn, tenant_id)
    try:
        hits = agents.shortlist(conn, tenant_id, str(artist["id"]), gate=gate)
    except fleet.LeadFailed as exc:
        print(f"cannot shortlist {artist['name']}: {exc}")
        return 1

    if not hits:
        print(f"no contactable counterparties embedded yet for {artist['name']}")
        return 0
    print(f"who to take {artist['name']} to:\n")
    for hit in hits:
        print(f"  {hit['distance']:.4f}  {hit['name'][:44]:44s} {hit['url'] or ''}")
    return 0


def retry_failed(conn: psycopg.Connection, tenant_id: str) -> int:
    """Un-park failed leads, clearing the attempt count that parked them.

    Attempts are reset rather than preserved: the run that failed five times failed
    against a cause that has now been removed, so carrying its strikes forward would
    park the lead again after one more unrelated hiccup.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE lead
                  SET state = 'pending', attempts = 0, owner_agent = NULL,
                      lease_expires_at = NULL, lease_token = NULL,
                      next_action_at = now(), updated_at = now()
                WHERE tenant_id = %s AND state = 'failed'""",
            (tenant_id,),
        )
        return cur.rowcount


#: Document platforms that are *evidence* and belong in `party_chunk`, as opposed to
#: `profile_v2`, which is the composed profile and deliberately does not.
#:
#: The distinction is the whole reason this constant exists rather than a `!=` in the
#: query. `profile_v2` is exactly the text `embed_party` writes into
#: `party.profile_embedding`, which is what R1 searches. Chunking it into `party_chunk`
#: would build a second vector index over the same sentences and call the duplication
#: retrieval — R2 would then "find evidence" that is a paraphrase of the thing that
#: ranked the party in the first place, which is worse than finding nothing because it
#: looks like corroboration.
#:
#: These four are the sources: an FCC licence row, a Radio Browser entry, a Wikipedia
#: article, a Deezer editor. They are what somebody would actually want to read when
#: asking "what do we already know about this party", and none of them is composed by us.
EVIDENCE_PLATFORMS: tuple[str, ...] = ("fcc", "radio_browser", "wikipedia", "deezer")


def embed_evidence(conn: psycopg.Connection, tenant_id: str) -> int:
    """Queue one `embed_document` lead per evidence document, so R2 has something to
    retrieve.

    `party_chunk` has been empty since it was created, and the reason is prosaic: the
    only writer of `embed_document` leads is the repo-document loader, which handles the
    two internal specs and nothing else. Every document discovery has written since —
    36,229 of them — arrived without anyone queueing the stage that chunks it. The
    `chunk_semantic` index has therefore never had a row, which made `agents.retrieve`
    a correct implementation of a query with no corpus.

    That is a *backlog*, not a missing writer, and the difference decided what happened
    to it. `028_drop_fact_semantic.sql` removed the other empty index because nothing
    wrote its column and no query read it. This one has both, and only needed the leads.

    Idempotent through `(tenant_id, target_hash)` like every other producer here, so
    running it twice queues nothing the second time. Documents whose body is empty are
    skipped in the query rather than queued and failed by the agent — `_fetch_embed_document`
    raises `LeadFailed(permanent=True)` on an empty body, and a frontier full of leads
    that are guaranteed to fail is a frontier nobody reads.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, kind, mode, adapter,
                                 target, target_hash, platform, reason, score)
               SELECT d.tenant_id, 'tenant', 'embed_document', 'auto', 'internal',
                      d.id::STRING, 'embed:' || d.content_hash, 'internal',
                      'evidence document, needs chunking for R2', 0.6
                 FROM party_document d
                WHERE d.tenant_id = %s
                  AND d.platform = ANY(%s)
                  AND length(trim(d.body)) > 0
               ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
            (tenant_id, list(EVIDENCE_PLATFORMS)),
        )
        return cur.rowcount


def requeue(conn: psycopg.Connection, tenant_id: str, kind: str,
            slug: str = "") -> int:
    """Return completed leads of one kind to the frontier, so their agent runs again.

    The hand-crank `--retry-failed` does not cover, and the distinction is the point:
    those leads did not fail. They **succeeded**, against a pipeline that has since got
    better — a classifier deployed where there was none, a floor retuned, style terms
    that were two coarse words and are now four specific ones.

    Every producer of leads in this system dedupes on `(tenant_id, target_hash)`, on
    purpose: `assets.queue_analysis` must not queue a second analysis when an upload is
    re-confirmed, and `--prospect` must not queue a second discovery every time it is
    run. The cost of that guarantee is that nothing re-queues one either. Both failure
    modes are silent — the command reports "queued 0" and everything looks fine — so
    this exists to make the re-run explicit rather than a hand-written DELETE.

    Safe as a flag rather than a migration because the agents supersede rather than
    overwrite: `analyse_recording` supersedes its facts, and discovery writes
    suggestions a human still approves. The earlier reading stays readable.

    `slug` narrows to one subject — a recording for `analyse_recording`, a party for
    the discovery kinds — because after deploying a classifier you want every master,
    and while debugging one track you emphatically do not.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE lead SET state = 'pending', attempts = 0, owner_agent = NULL,
                      lease_expires_at = NULL, lease_token = NULL,
                      next_action_at = now(), updated_at = now()
                WHERE tenant_id = %s AND kind = %s AND state != 'pending'
                  AND (%s = ''
                       OR recording_id IN (SELECT id FROM recording
                                            WHERE tenant_id = %s AND slug = %s)
                       OR party_id IN (SELECT id FROM party
                                        WHERE tenant_id = %s AND slug = %s))""",
            (tenant_id, kind, slug, tenant_id, slug, tenant_id, slug),
        )
        return cur.rowcount


def reanalyse(conn: psycopg.Connection, tenant_id: str, slug: str = "") -> int:
    """`requeue` for the common case: re-read every stored master."""
    return requeue(conn, tenant_id, "analyse_recording", slug)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--tenant", help="tenant slug, if more than one exists")
    parser.add_argument("--drain-only", action="store_true",
                        help="work the existing frontier without loading anything")
    parser.add_argument("--seed-sources", action="store_true",
                        help="queue a map_source lead for every unmapped presence row")
    parser.add_argument("--retry-failed", action="store_true",
                        help="return parked leads to the frontier (after fixing the cause)")
    parser.add_argument("--reanalyse", nargs="?", const="", default=None,
                        metavar="SLUG",
                        help="re-read stored masters (optionally one recording slug) "
                             "after the analysis pipeline has changed")
    parser.add_argument("--requeue", metavar="KIND[:SLUG]",
                        help="return completed leads of one kind to the frontier, e.g. "
                             "find_counterparties, or find_counterparties:hallow-youth")
    parser.add_argument("--discover", action="store_true",
                        help="search every enabled keyless source for every artist")
    parser.add_argument("--prospect", action="store_true",
                        help="profile each roster artist and find counterparties for them")
    parser.add_argument("--index-stations", nargs="?", const="", default=None,
                        metavar="STATES",
                        help="build the standing radio counterparty index from the FCC "
                             "register; optionally a comma-separated list of "
                             "jurisdictions, e.g. RI,MA. Default is all 56.")
    parser.add_argument("--band", default="nce", choices=("nce", "commercial", "all"),
                        help="which FM band --index-stations should read. 'nce' is "
                             "college and community radio; 'commercial' is 92.1 up.")
    parser.add_argument("--index-streams", nargs="?", const="", default=None,
                        metavar="COUNTRIES",
                        help="pull genre tags and websites from Radio Browser, "
                             "enriching stations already indexed and adding the rest. "
                             "Comma-separated ISO country codes; default US.")
    parser.add_argument("--index-podcasts", nargs="?", const="", default=None,
                        metavar="FIRST-LAST",
                        help="index music podcasts from Podcast Index as "
                             "counterparties. Optionally a feed-ID range, e.g. 0-20000 "
                             "— which is how to exercise the stage without seeding the "
                             "whole four-million-feed index. Default is the whole "
                             "index. Needs PODCASTINDEX_API_KEY and "
                             "PODCASTINDEX_API_SECRET.")
    parser.add_argument("--refresh-streams", action="store_true",
                        help="re-read the Radio Browser entries already indexed and "
                             "store the country, market and language the first pass "
                             "dropped. The country is what a contact route needs before "
                             "it may be written at all.")
    parser.add_argument("--harvest-contacts", action="store_true",
                        help="read each counterparty's own website and write the contact "
                             "routes it publishes. Only published addresses are written; "
                             "nothing is guessed from a domain.")
    parser.add_argument("--harvest-feed-contacts", action="store_true",
                        help="read each indexed podcast's own RSS feed and write the "
                             "owner address it declares in <itunes:owner>. Needs shows "
                             "in the index first — see --index-podcasts.")
    parser.add_argument("--embed-evidence", action="store_true",
                        help="queue chunking for every evidence document, so R2 has a corpus")
    parser.add_argument("--recompose-profiles", action="store_true",
                        help="rewrite every counterparty's embedded profile text with "
                             "the current composition rules, and re-queue its embedding")
    parser.add_argument("--self-test", metavar="EMAIL",
                        help="prepare one real pitch to this address through the "
                             "production campaign/thread/draft/approve path, and stop "
                             "at the gate. Delivery is a separate --send.")
    parser.add_argument("--close-thread", nargs=2, metavar=("THREAD_ID", "OUTCOME"),
                        help="close a thread (closed_won|closed_lost|closed_no_reply); "
                             "this is what queues distil_lesson and writes a lesson")
    parser.add_argument("--send", action="store_true",
                        help="drain the outbox: actually mail the approved pitches. "
                             "Irreversible. Requires PLATFORM_MAIL_* and "
                             "RTF_PAID_ENABLED=1.")
    parser.add_argument("--enrich-genre", action="store_true",
                        help="read station formats from Wikipedia for every indexed "
                             "station that still has no genre, and re-embed each one")
    parser.add_argument("--shortlist", help="artist slug: who should we take them to?")
    parser.add_argument("--kinds", help="comma-separated lead kinds to work")
    parser.add_argument("--search", help="run a retrieval against what is indexed")
    parser.add_argument("--worker", default="ingest-cli")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")

    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        tenant_id = _tenant(conn, args.tenant)

        if args.search:
            gate = spend.Gate.open(conn, tenant_id)
            hits = agents.retrieve(conn, tenant_id, args.search, gate=gate)
            if not hits:
                print("no hits — is anything embedded yet?")
                return 0
            for hit in hits:
                snippet = " ".join(hit["text"].split())[:220]
                print(f"\n  {hit['distance']:.4f}  {hit['title']} #{hit['ordinal']}")
                print(f"          {snippet}…")
            return 0

        if args.paths:
            print(f"loading {len(args.paths)} file(s)")
            documents, leads = load(conn, tenant_id, args.paths)
            print(f"  {documents} new document(s), {leads} new lead(s)")

        if args.retry_failed:
            # A parked lead is parked because a human needs to act. Once they have —
            # added a key, enabled a source — nothing else knows to un-park it, and
            # `--seed-sources` will not, because the lead already exists and its
            # target_hash conflicts. This is that hand-crank, deliberately explicit.
            retried = retry_failed(conn, tenant_id)
            print(f"returned {retried} parked lead(s) to the frontier")

        if args.requeue:
            kind, _, only = args.requeue.partition(":")
            if kind not in agents.REGISTRY:
                sys.exit(f"{kind!r} is not an agent kind. Known: "
                         f"{', '.join(sorted(agents.REGISTRY))}")
            moved = requeue(conn, tenant_id, kind, only)
            print(f"re-queued {moved} {kind} lead(s) for {only or 'every subject'}")

        if args.reanalyse is not None:
            requeued = reanalyse(conn, tenant_id, args.reanalyse)
            scope = args.reanalyse or "every recording"
            print(f"re-queued {requeued} analyse_recording lead(s) for {scope}")

        if args.seed_sources:
            seeded = seed_sources(conn, tenant_id)
            print(f"seeded {seeded} map_source lead(s) from presence rows")

        if args.shortlist:
            return show_shortlist(conn, tenant_id, args.shortlist)

        if args.discover:
            found = discover_sources(conn, tenant_id)
            print(f"queued {found} discovery search(es) across keyless sources")

        if args.prospect:
            queued = prospect(conn, tenant_id)
            print(f"queued {queued} profiling / prospecting lead(s)")

        if args.index_stations is not None:
            states = [s.strip().upper()
                      for s in args.index_stations.split(",") if s.strip()] or None
            queued = index_stations(conn, tenant_id, states, band=args.band)
            scope = ", ".join(states) if states else "every jurisdiction"
            print(f"queued {queued} index_stations lead(s) for {scope} ({args.band})")

        if args.index_streams is not None:
            countries = [c.strip().upper()
                         for c in args.index_streams.split(",") if c.strip()] or None
            queued = index_streams(conn, tenant_id, countries)
            print(f"queued {queued} index_streams lead(s) for "
                  f"{', '.join(countries) if countries else 'US'}")

        if args.index_podcasts is not None:
            # Both failures exit rather than raise: a malformed range and a missing key
            # are things an operator fixes in the next shell line, and a traceback is a
            # worse way to say either. Neither is defaulted around.
            try:
                first, ceiling = feed_id_range(args.index_podcasts)
                queued = index_podcasts(conn, tenant_id, start=first, ceiling=ceiling)
            except (ValueError, podcastindex.NotConfigured, podcastindex.Refused) as exc:
                sys.exit(str(exc))
            scope = (f"feed IDs {first}–{ceiling}" if ceiling is not None
                     else "the whole index")
            print(f"queued {queued} index_podcasts lead(s) for {scope}")

        if args.enrich_genre:
            queued = enrich_genre(conn, tenant_id)
            print(f"queued {queued} enrich_genre lead(s)")

        if args.refresh_streams:
            queued = refresh_streams(conn, tenant_id)
            print(f"queued {queued} refresh_stream lead(s)")

        if args.harvest_contacts:
            queued = harvest_contacts(conn, tenant_id)
            print(f"queued {queued} harvest_contacts lead(s)")
            if not queued:
                # Zero is ambiguous — every candidate is already queued, or no candidate
                # has country evidence — and the two need opposite next actions. Saying
                # which is what stops an operator concluding the index has no websites.
                print("  (nothing new: either every candidate is already queued, or no "
                      "counterparty with a website has country evidence yet — run "
                      "--refresh-streams first)")

        if args.harvest_feed_contacts:
            queued = harvest_feed_contacts(conn, tenant_id)
            print(f"queued {queued} harvest_feed_contacts lead(s)")
            if not queued:
                print("  (no podcast shows are indexed yet — this stage reads a feed "
                      "`index_podcasts` has already created a party for)")

        if args.embed_evidence:
            queued = embed_evidence(conn, tenant_id)
            print(f"queued {queued} embed_document lead(s) over evidence documents")

        if args.recompose_profiles:
            done = recompose_profiles(conn, tenant_id)
            print(f"recomposed {done} profile(s); embeddings re-queued")

        if args.self_test:
            thread_id = send_self_test(conn, tenant_id, args.self_test.strip())
            print(f"prepared. thread={thread_id}")
            print("outbox row is pending; deliver it with --send")

        if args.close_thread:
            thread_id, outcome = args.close_thread
            if outcome not in outreach.CLOSED:
                sys.exit(f"{outcome!r} is not terminal; "
                         f"one of {', '.join(sorted(outreach.CLOSED))}")
            outreach.advance(conn, tenant_id, thread_id, outcome)
            print(f"thread {thread_id} -> {outcome}; distil_lesson queued")

        if args.send:
            # Deliberately not folded into `drain`. Everything drain works is retryable;
            # this is not, and it should not be possible to send by accident while
            # meaning to embed something.
            try:
                delivered = sender.send_once(conn, tenant_id, args.worker)
            except mail.MailNotConfigured as exc:
                sys.exit(str(exc))
            print(f"sent {delivered} message(s)")

        if (args.paths or args.drain_only or args.seed_sources or args.retry_failed
                or args.discover or args.prospect or args.reanalyse is not None
                or args.requeue or args.index_stations is not None
                or args.index_streams is not None or args.enrich_genre
                or args.index_podcasts is not None
                or args.refresh_streams or args.harvest_contacts
                or args.harvest_feed_contacts
                or args.close_thread):
            print("draining the frontier")
            kinds = args.kinds.split(",") if args.kinds else None
            total = drain(conn, tenant_id, worker_name=args.worker, kinds=kinds)
            print(f"  {total} lead(s) worked")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
