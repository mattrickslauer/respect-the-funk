"""Which surface each counterparty was indexed from, and how many we hold on each.

## Why this is a module and not a `GROUP BY presence.platform`

Because `presence.platform` does not answer the question. It says where a party has a
*page* — 34,100 rows all reading `web`, which is true of a college FM licensee and of an
internet-only stream alike and distinguishes neither. What the public map has to say is
which **index** a counterparty came out of, and that is carried by
`party_identifier.kind`: a station the FCC licensed holds an `fcc_facility_id`, one Radio
Browser knows holds a `radiobrowser_uuid`, and a playlist editor holds neither.

So the classification lives here, once, in the same spirit as `plans.py`: the landing
page renders these labels, the public search filters on these keys, and both read the
same tuple rather than each spelling `fcc_facility_id` into their own SQL.

## Every party lands in exactly one bucket

`CLASSIFY` is a `CASE`, not a set of counts, and the order of its branches is the whole
of its correctness. 384 stations carry *both* an `fcc_facility_id` and a
`radiobrowser_uuid` — Radio Browser knows the same broadcaster the Commission licensed,
which is the join `index_streams` was built to make. Counted per-identifier they appear
twice, the platform totals sum to more than the index holds, and the map's own arithmetic
contradicts the counter above it.

FM wins that tie because the licence is the stronger claim: it is a government record of
a transmitter, where the Radio Browser row is a community entry about a stream. A station
that is both is a licensed station we also have a stream for.

## The platforms with no rows are the point, not an oversight

`indexed = False` entries ship with a count of zero and are rendered greyed rather than
hidden. This is `source_manifest.disabled_reason`'s discipline — *"we asked and it is not
there"* must not look like *"we never asked"* — applied to a public page: a reader
looking at the UGC platforms is being told plainly that we hold nothing there yet, rather
than being left to infer it from an absence. Deleting these rows to make the page look
fuller would be the one edit this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class Platform:
    """One surface counterparties are indexed from.

    Frozen for `plans.Tier`'s reason: this is a description of what the index contains,
    and code that can edit the category it is counting is code that can invent one.
    """

    #: Stored nowhere. Computed by `CLASSIFY` and used as the filter value on
    #: `/public/search?platform=`, so changing one breaks a URL somebody may have.
    key: str

    #: What the page prints.
    label: str

    #: Names the `<symbol>` in the landing page's inline SVG sprite. A key rather than
    #: markup: the sprite is a design asset that belongs in the template, and a module
    #: that returned `<path d="…">` would put drawing instructions in the data layer.
    mark: str

    #: One line under the count. Says what the source *is*, because "Internet radio —
    #: 34,597" invites the reader to assume we scraped something.
    blurb: str

    #: Whether this build indexes it at all. `False` means the count is zero by
    #: construction and the UI must say so — see this module's docstring.
    indexed: bool


#: In the order the platform strip shows them: what we hold most of first, then the
#: surfaces that are next. A tuple, so the order is part of the definition.
PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="internet_radio", label="Internet radio", mark="stream",
        blurb="Community-listed stations from the Radio Browser directory.",
        indexed=True),
    Platform(
        key="fm_radio", label="FM radio", mark="tower",
        blurb="Licensed transmitters, transcribed from the FCC register.",
        indexed=True),
    Platform(
        key="deezer", label="Deezer", mark="deezer",
        blurb="Editorial playlist curators.",
        indexed=True),
    Platform(
        key="spotify", label="Spotify", mark="spotify",
        blurb="Editorial playlist curators.",
        indexed=True),
    Platform(
        key="podcast", label="Podcasts", mark="podcast",
        blurb="Music shows and their hosts. Feeds read, no counterparties written yet.",
        indexed=False),
    Platform(
        key="tiktok", label="TikTok", mark="tiktok",
        blurb="UGC creators. Not indexed yet.", indexed=False),
    Platform(
        key="youtube", label="YouTube", mark="youtube",
        blurb="UGC creators. Not indexed yet.", indexed=False),
    Platform(
        key="instagram", label="Instagram", mark="instagram",
        blurb="UGC creators. Not indexed yet.", indexed=False),
)

BY_KEY: dict[str, Platform] = {p.key: p for p in PLATFORMS}

#: The keys a public query may filter on. `other` is deliberately absent: it is a bucket
#: this module produces for rows it cannot classify, not a surface anybody indexed, and
#: offering it as a filter would invite a reader to browse our failures.
KEYS: frozenset[str] = frozenset(BY_KEY)


#: Three pre-aggregated CTEs and a `CASE` over them: the classification, as one query.
#:
#: ## Why it is shaped like this and not as four `EXISTS` subqueries
#:
#: Because the obvious version does not finish. Written as correlated `EXISTS` clauses —
#: which reads far better, and was the first draft — this took **over 120 seconds against
#: the live cluster** and was abandoned at the timeout: four correlated subqueries plus a
#: correlated scalar, each re-executed per row, over 43,191 rows on a distributed store.
#: Pre-aggregating each side once and hash-joining brings the same answer back in **0.84
#: seconds**, measured 2026-08-15. That is a 140x difference and it is the reason this
#: constant is ugly.
#:
#: The branch order in the `CASE` is load-bearing — see the module docstring on the 384
#: stations that are both FM and Radio Browser. `other` is the honest name for a party
#: carrying none of these markers; it is counted rather than dropped, which is what lets
#: `totals()["total"]` be checked against a plain `SELECT count(*)`. It came back as
#: exactly 43,191 when it was.
#:
#: `DISTINCT ON` in `ctry` resolves the case where a party holds two live `country_code`
#: facts. That is legal: `fact_one_live_per_dimension` is unique on
#: `(tenant, party, dimension, provenance)`, so the `measured` country written from the
#: FCC register and the `asserted` one Radio Browser supplies coexist on the ~384 rows
#: both sources know. `SCOPE-RESET §2a` settles which wins — *an inferred value may never
#: overwrite a measured one* — and the `ORDER BY` is that rule expressed as a sort.
#: Ordering by `provenance` alphabetically would have picked `asserted`, silently, and
#: been wrong in precisely the rows where the two disagree.
CLASSIFIED = """
WITH ident AS (
  SELECT party_id,
         max(CASE WHEN kind = 'fcc_facility_id'   THEN 1 ELSE 0 END) AS fcc,
         max(CASE WHEN kind = 'radiobrowser_uuid' THEN 1 ELSE 0 END) AS rb
    FROM party_identifier
   WHERE tenant_id = %(t)s
     AND kind IN ('fcc_facility_id', 'radiobrowser_uuid')
   GROUP BY party_id),
pres AS (
  SELECT subject_id AS party_id,
         max(CASE WHEN platform = 'spotify' THEN 1 ELSE 0 END) AS sp,
         max(CASE WHEN platform = 'deezer'  THEN 1 ELSE 0 END) AS dz
    FROM presence
   WHERE tenant_id = %(t)s AND platform IN ('spotify', 'deezer')
   GROUP BY subject_id),
ctry AS (
  SELECT DISTINCT ON (party_id) party_id, value_text
    FROM party_fact
   WHERE tenant_id = %(t)s AND dimension = 'country_code' AND status = 'live'
   ORDER BY party_id, CASE provenance WHEN 'measured' THEN 0
                                      WHEN 'asserted' THEN 1
                                      ELSE 2 END),
ctc AS (
  SELECT party_id, count(*) AS n
    FROM contact_route
   WHERE tenant_id = %(t)s
   GROUP BY party_id)
"""

#: The `CASE` itself, separated so `totals` and `search` interpolate the same branches.
PLATFORM_CASE = """
       CASE WHEN coalesce(ident.fcc, 0) = 1 THEN 'fm_radio'
            WHEN coalesce(ident.rb,  0) = 1 THEN 'internet_radio'
            WHEN coalesce(pres.sp,   0) = 1 THEN 'spotify'
            WHEN coalesce(pres.dz,   0) = 1 THEN 'deezer'
            ELSE 'other' END
"""

#: The three `LEFT JOIN`s the `CASE` above reads. Left, not inner: a party with no
#: identifier row at all must still appear, as `other`, rather than vanishing from a
#: total that claims to count the whole index.
JOINS = """
  LEFT JOIN ident ON ident.party_id = p.id
  LEFT JOIN pres  ON pres.party_id  = p.id
  LEFT JOIN ctry  ON ctry.party_id  = p.id
  LEFT JOIN ctc   ON ctc.party_id   = p.id
"""


def totals(conn: psycopg.Connection, tenant_id: str) -> dict[str, Any]:
    """Counterparties per country and per platform, in one pass over the index.

    Returns `{"total", "located", "countries": [...], "platforms": [...]}`, which is
    everything the public map draws. One query rather than one per panel: the counter,
    the choropleth and the platform strip are three renderings of the same `GROUP BY`,
    and computing them separately is how a page ends up showing a total that does not
    equal the sum of its own map.

    `located` is stated alongside `total` rather than left to be derived, because the
    difference between them is a real gap — counterparties we hold and cannot place —
    and a map that silently drops them would overstate its own coverage. The template
    prints both.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""{CLASSIFIED}
                SELECT ctry.value_text AS cc, {PLATFORM_CASE} AS platform,
                       count(*) AS n, min(p.created_at) AS first_at
                  FROM party p {JOINS}
                 WHERE p.tenant_id = %(t)s AND p.party_class = 'counterparty'
                 GROUP BY 1, 2""",
            {"t": tenant_id},
        )
        rows = cur.fetchall()

    by_country: dict[str, dict[str, Any]] = {}
    by_platform: dict[str, int] = {p.key: 0 for p in PLATFORMS}
    total = located = 0

    for row in rows:
        code, key, count = row["cc"], row["platform"], int(row["n"])
        total += count
        if key in by_platform:
            by_platform[key] += count
        if not code:
            # No country fact. Counted in `total`, absent from `countries`, and the
            # difference is what `located` reports. Not bucketed into a placeholder
            # country, which would put dots on the map for rows we cannot place.
            continue
        located += count
        entry = by_country.setdefault(
            code, {"cc": code, "n": 0, "platforms": {}, "first_at": None})
        entry["n"] += count
        entry["platforms"][key] = entry["platforms"].get(key, 0) + count
        # When this country first appeared in the index, as the earliest `created_at`
        # across its platform groups. The map's load sequence lights countries in this
        # order, so it replays the acquisition as it actually happened rather than
        # animating in whatever order the rows came back — which would look identical
        # and mean nothing.
        seen = row["first_at"]
        if seen is not None and (entry["first_at"] is None or seen < entry["first_at"]):
            entry["first_at"] = seen

    for entry in by_country.values():
        # Serialised here rather than left as a datetime, because this dict goes
        # straight out as JSON and a `datetime` is the one type that would make the
        # endpoint fail only once a country had a timestamp.
        entry["first_at"] = (entry["first_at"].isoformat()
                             if entry["first_at"] is not None else None)

    return {
        "total": total,
        "located": located,
        "countries": sorted(by_country.values(), key=lambda c: -c["n"]),
        "platforms": [
            {"key": p.key, "label": p.label, "mark": p.mark, "blurb": p.blurb,
             "indexed": p.indexed, "n": by_platform[p.key]}
            for p in PLATFORMS
        ],
    }


#: The most rows one public search will return. A page size, and also the only thing
#: standing between a public endpoint and somebody paging the whole index out of it one
#: request at a time. It is not a security control and is not claimed as one — the index
#: is public record either way — it is what stops a single query costing the cluster more
#: than the page it draws.
PAGE = 50

#: The facts a stranger may read. A closed allow-list, not an exclusion list, because the
#: two fail in opposite directions: a dimension added tomorrow is invisible here until
#: somebody names it, whereas a deny-list would publish it the moment it was written.
#: Every one of these is a matter of public record — a licence field, a community tag, or
#: a genre the station itself advertises.
PUBLIC_DIMENSIONS: tuple[str, ...] = (
    "market", "genre", "station_kind", "licensee", "frequency_mhz", "language",
)


def search(conn: psycopg.Connection, tenant_id: str, *, country: str = "",
           platform: str = "", q: str = "", limit: int = PAGE,
           offset: int = 0) -> dict[str, Any]:
    """Counterparties matching a public query, with public facts and a contact *count*.

    ## What this deliberately does not return

    `contact_route.value` — the address itself — never leaves this function. Only how
    many routes we hold. That is the line the whole endpoint is drawn around: the index
    is assembled from public record and showing what is in it costs nothing, but the
    contact addresses are the asset, and an endpoint that hands them to an anonymous
    caller is a mailing list with a search box.

    The count is included because it says something true and useful — this station is
    reachable, we did the work — without being the thing itself. `test_public_surface`
    asserts the absence directly rather than trusting this docstring.

    ## Two queries, not three, and not one

    The filter runs first and returns at most `limit` party ids; the facts and the
    contact counts are then fetched for exactly those. Joining them into the filter would
    multiply each party by its fact rows before the `LIMIT` applied, so a station with
    six facts would consume six of the fifty. This shape also keeps the expensive text
    match on one narrow column instead of on a joined product.

    The match total comes back as `count(*) OVER ()` on the same pass rather than from a
    separate `SELECT count(*)`. That is worth a note because the obvious version — one
    query for the count, one for the page — was what shipped first, and it ran the
    classification CTEs twice: measured end to end, opening a country panel took **3.9
    seconds**, of which the duplicate count was about half. The window function gets the
    same number for free, because the rows are already being scanned.

    Its one wrinkle: a window count rides on the returned rows, so when a filter matches
    nothing there is no row to carry it and `matched` is 0. That happens to be the right
    answer, which is why this is a note and not a bug.

    ## Why the order is contact routes first

    Not alphabetical, which is what this did first and which is actively bad here: names
    in this index begin with quotes, hashes and full stops, so `ORDER BY name` opens
    every country with `"The Mighty"1290 GLI` and `#ds106radio`. Sorting by how many
    contact routes we hold puts the counterparties we have actually done work on at the
    top — the ones with a published address, a market and a genre — which is both a
    better first impression and the more useful answer to *what do you have where I am*.
    Name breaks the tie, so the order is still stable across pages.
    """
    limit = max(1, min(int(limit), PAGE))
    offset = max(0, int(offset))

    params: dict[str, Any] = {"t": tenant_id, "lim": limit, "off": offset}

    # `filters` carries only the *optional* predicates. The tenant and party-class
    # predicates are written literally into the statement below and never travel through
    # here, which is not a style preference: `test_tenant_scoping` reads these statements
    # statically, and a `WHERE` assembled entirely from a runtime list is one it cannot
    # see into — it failed exactly that way when this function built the whole clause as
    # a joined list. A tenant predicate that a reader (or a test) has to run the program
    # to find is one nobody can review.
    filters = ""
    if country:
        filters += " AND ctry.value_text = %(cc)s"
        params["cc"] = country.strip().upper()[:2]
    if platform:
        if platform not in KEYS:
            raise ValueError(
                f"{platform!r} is not a platform this build indexes. Known: "
                f"{', '.join(sorted(KEYS))}.")
        filters += f" AND {PLATFORM_CASE.strip()} = %(pf)s"
        params["pf"] = platform
    if q.strip():
        # Name only. Searching the fact text as well reads as an obvious improvement and
        # is left out on purpose: `party_fact` has no index that serves a leading
        # wildcard, so it turns a 43,000-row scan into a 100,000-row one for a match the
        # country and platform filters already narrow better.
        filters += " AND p.name ILIKE %(q)s"
        params["q"] = f"%{q.strip()[:80]}%"

    with conn.cursor() as cur:
        cur.execute(
            f"""{CLASSIFIED}
                SELECT p.id, p.name, p.slug, p.contact_state,
                       ctry.value_text AS cc, {PLATFORM_CASE} AS platform,
                       coalesce(ctc.n, 0) AS contacts,
                       count(*) OVER () AS matched
                  FROM party p {JOINS}
                 WHERE p.tenant_id = %(t)s
                   AND p.party_class = 'counterparty'{filters}
                 ORDER BY coalesce(ctc.n, 0) DESC, p.name
                 LIMIT %(lim)s OFFSET %(off)s""",
            params)
        rows = cur.fetchall()
    matched = int(rows[0]["matched"]) if rows else 0

    ids = [r["id"] for r in rows]
    facts: dict[Any, dict[str, str]] = {i: {} for i in ids}
    contacts: dict[Any, int] = {r["id"]: int(r["contacts"]) for r in rows}
    if ids:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT party_id, dimension, value_text, provenance
                     FROM party_fact
                    WHERE tenant_id = %s AND party_id = ANY(%s)
                      AND status = 'live' AND dimension = ANY(%s)""",
                (tenant_id, ids, list(PUBLIC_DIMENSIONS)))
            for row in cur.fetchall():
                # Provenance travels with the value, because `SCOPE-RESET §2a` requires
                # the three classes to render distinguishably and a client cannot do that
                # from a bare string. `station_kind` in particular is inferred, and a page
                # showing it beside a transcribed licensee without saying so is the exact
                # failure that rule exists to prevent.
                facts[row["party_id"]][row["dimension"]] = {
                    "value": row["value_text"], "provenance": row["provenance"]}

    return {
        "matched": matched,
        "limit": limit,
        "offset": offset,
        "rows": [{
            "name": r["name"],
            "slug": r["slug"],
            "country": r["cc"] or "",
            "platform": r["platform"],
            "facts": facts.get(r["id"], {}),
            # A number, never the routes themselves. See this function's docstring.
            "contacts": contacts.get(r["id"], 0),
        } for r in rows],
    }


def history(conn: psycopg.Connection, tenant_id: str, *, days: int = 30) -> list[dict]:
    """Counterparties indexed per day, oldest first, for the growth sparkline.

    Read from `party.created_at` rather than kept as a running counter, because a counter
    is a second copy of a number the rows already carry and would drift the first time a
    backfill wrote rows without touching it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date_trunc('day', created_at)::date AS d, count(*) AS n
                 FROM party
                WHERE tenant_id = %s AND party_class = 'counterparty'
                  AND created_at > now() - (%s * INTERVAL '1 day')
                GROUP BY 1 ORDER BY 1""",
            (tenant_id, days),
        )
        return [{"d": r["d"].isoformat(), "n": int(r["n"])} for r in cur.fetchall()]
