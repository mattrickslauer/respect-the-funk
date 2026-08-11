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

import psycopg
from psycopg.rows import dict_row

from rtf_platform import agents, fleet, spend


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

        if (args.paths or args.drain_only or args.seed_sources or args.retry_failed
                or args.discover or args.prospect or args.reanalyse is not None
                or args.requeue):
            print("draining the frontier")
            kinds = args.kinds.split(",") if args.kinds else None
            total = drain(conn, tenant_id, worker_name=args.worker, kinds=kinds)
            print(f"  {total} lead(s) worked")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
