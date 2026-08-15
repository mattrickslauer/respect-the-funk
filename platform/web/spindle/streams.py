"""Re-reading a Radio Browser entry we already hold, for the fields nobody wrote down.

`index_streams` was built to answer one question — *what does this station play* — and it
answered it. What it did with the rest of the payload is the subject of this module.
Measured against the live cluster on 2026-08-13:

    counterparties                                    14,170
    with an identical embedded profile document        1,651
    of those, carrying a measured `radiobrowser_uuid`  1,625

Those 1,651 rows all hold the same forty-four characters — `Programming not documented. A
radio station.` — so they embed to the same vector and sit at the same cosine distance
from every query the shortlist will ever run. They are in the index and they are not
*rankable*: no query can prefer one over another, because there is nothing in any of them
to prefer. `profiles.py` rule 1 is about text every row shares diluting what does not; this
is the limit case, where the shared text is the entire document.

They are not rankable because nobody wrote down what the source already said about them.


## What Radio Browser publishes and `index_streams` did not store

Its station payload carries `countrycode`, `state`, `language` and `homepage` alongside
`tags`. `_write_index_streams` writes the tags as a `genre` fact and the homepage as a
`presence`, and drops the other three on the floor — they reached `radiobrowser.profile_text`
as prose and never became facts, so `profiles.from_facts`, which is the rebuild path,
cannot see them.

Sampled against the live API on 2026-08-13, forty of the untagged parties by UUID:

    countrycode   40/40        state      13/40
    homepage      39/40        language    5/40
    tags           0/40

The last line is the one to be honest about. **Radio Browser will not make these stations
rankable**, because it does not know what they play and re-reading it does not change
that. What this stage buys is narrower and real:

  * `country_code` on essentially every row, which is what `024_regional_by_row.sql`
    requires before a contact route for that station may be written at all — see
    `contacts.country_for`. Today that gates 5,442 counterparties which have a homepage
    and no FCC licence, i.e. no way to establish a country. This is the unblocking.
  * `market` on about a third and `language` on about an eighth, which splits some of the
    1,651-way tie into groups a query can actually distinguish.
  * `genre` for any station the community has tagged since it was first indexed. Zero in
    the sample, and included anyway because it costs nothing and the alternative is a
    stage that goes stale by construction.

Roughly eleven hundred rows are expected to keep the identical document afterwards. That
is not a shortfall this module is hiding: there is no evidence anywhere we can lawfully
reach that says what an untagged internet stream plays, and `026_role_backfill.sql`
already made the argument for the honest response — *"a party with no facts has nothing to
compose a profile from either; the text it would have received was UNKNOWN plus an
invented role, which is strictly worse than no text and a name in a report."* Guessing a
genre from `The Hairband Channel` is the same forbidden inference with a different input.


## Why a re-read and not a parse of what we stored

The state and the language are, technically, still recoverable from the `radio_browser`
`party_document` body — `radiobrowser.profile_text` wrote them into a sentence this
codebase composed. Parsing our own generated prose back into fields is possible and is
rejected: the format is not a format, it is a docstring's worth of implicit contract that
any edit to `profile_text` silently breaks, and the failure would be a fact quietly
missing rather than an error. Radio Browser is the system of record for its own fields and
answers by UUID for free.

Provenance is `asserted`, matching `radiobrowser.py`'s standing judgement about its own
source: this module derives nothing, it copies a string somebody typed into a community
database. `026` calls a Radio Browser entry's *existence* measured — the crawler checked
the stream responds — and that is a different claim from its `state` field being right.
The country in particular is a proxy for where a person is, and `024` says the thing to do
with a proxy is state it rather than hide it.

Stdlib only, like the module it re-reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from typing import Any

import psycopg
from psycopg.types.json import Json

from spindle import fleet, profiles, radiobrowser, spend

#: Stations one lead will look up and write. Bounded like `agents.GENRE_CAP`, and for the
#: same reason: a bucket bigger than this is finished by the lead's *next* run, because the
#: selection is "has no `country_code` fact yet" rather than a cursor, so a partial pass
#: loses nothing. 200 is four API calls and 200 small transactions, which commits inside a
#: 300-second lease on this cluster with room to spare.
REFRESH_CAP = 200

#: UUIDs per API call. Radio Browser's `byuuid` accepts a comma-separated `uuids` query
#: parameter — verified against the live API on 2026-08-13, returning both stations for a
#: two-UUID request — and 50 keeps the URL under two kilobytes, which is the length no
#: proxy in the path has ever been known to truncate.
UUIDS_PER_CALL = 50

#: The sixteen buckets work is split into: the first hex character of the station UUID.
#: Deterministic, so a re-run addresses the same stations; naturally even, because a UUID's
#: first nibble is random; and independent of any ordering, so two workers on two buckets
#: never contend. `enrich_genre` buckets by call-sign prefix for exactly these reasons and
#: this is that pattern with the identifier this stage actually has.
BUCKETS = tuple("0123456789abcdef")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def by_uuid(uuids: list[str], *, timeout: int = 60) -> dict[str, dict[str, Any]]:
    """Radio Browser's current record for each UUID, keyed by UUID.

    A UUID the API does not return is **absent from the result** rather than present with
    empty fields. That distinction is the whole reason this returns a mapping: a station
    delisted since we indexed it and a station whose `state` is blank are different facts,
    and a caller that cannot tell them apart writes the second as though it were the first.
    """
    if not uuids:
        return {}
    query = urllib.parse.urlencode({"uuids": ",".join(uuids)})
    request = urllib.request.Request(
        f"{radiobrowser.BASE}/json/stations/byuuid?{query}",
        headers={"User-Agent": radiobrowser.USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return {row["stationuuid"]: row for row in payload if row.get("stationuuid")}


def country_code(station: dict[str, Any]) -> str:
    """The station's ISO-3166-1 alpha-2 country code, or `''`.

    Shape-checked here rather than trusted, because this value ends up deciding where a
    person's contact details are physically stored. `024_regional_by_row.sql` spends a
    section on the alpha-2 trap — a US *state* code is also two capital letters — and the
    protection against it is that this reads `countrycode`, which is ISO by definition in
    Radio Browser's schema, and never `state`, which is not.
    """
    code = (station.get("countrycode") or "").strip().upper()
    return code if len(code) == 2 and code.isalpha() else ""


def market(station: dict[str, Any]) -> str:
    """`"Nevada, The United States Of America"`, or `''` when the state is blank.

    **A country alone is not a market and is not written.** 40 of 40 sampled stations
    carry a country and 13 carry a state, so a country-only market would add
    `Broadcasts to The United States Of America.` to thousands of documents — a sentence
    that every one of them shares, which is `profiles.py` rule 1 being violated by the
    module that exists to repair it. A market that does not narrow anything is noise
    wearing a fact's clothes.
    """
    state = (station.get("state") or "").strip()
    if not state:
        return ""
    country = (station.get("country") or "").strip()
    return f"{state}, {country}" if country else state


def language(station: dict[str, Any]) -> str:
    """The station's language as the community recorded it, lowercased, or `''`.

    Lowercased for the reason `radiobrowser.genres` lowercases: `English` and `english`
    are one language and two buckets.
    """
    return " ".join((station.get("language") or "").strip().lower().split())


# ------------------------------------------------------------ the refresh_stream stage

def _fetch_refresh_stream(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate) -> dict[str, Any]:
    """Re-read one bucket of station UUIDs. Reads the database, writes nothing.

    Selecting here rather than in `write` is deliberate and is the pattern
    `_fetch_enrich_genre` established: `NetworkAgent.fetch` runs with no transaction open,
    so this is an autocommitted `SELECT`, and the HTTP round trips happen outside the
    transaction that later commits the facts.
    """
    bucket = str(lead.get("target") or "").strip().lower()
    if bucket not in BUCKETS:
        raise fleet.LeadFailed(
            f"{bucket!r} is not a UUID bucket; expected one hex character",
            permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT i.value AS uuid, i.party_id, p.name
                 FROM party_identifier i
                 JOIN party p ON p.tenant_id = i.tenant_id AND p.id = i.party_id
                WHERE i.tenant_id = %s AND i.kind = 'radiobrowser_uuid'
                  AND i.value LIKE %s
                  AND p.party_class = 'counterparty'
                  AND NOT EXISTS (SELECT 1 FROM party_fact f
                                   WHERE f.tenant_id = i.tenant_id
                                     AND f.party_id = i.party_id
                                     AND f.dimension = 'country_code')
                ORDER BY i.value
                LIMIT %s""",
            (lead["tenant_id"], f"{bucket}%", REFRESH_CAP),
        )
        rows = cur.fetchall()

    wanted = [r["uuid"] for r in rows if _UUID.match(r["uuid"])]
    found: dict[str, dict[str, Any]] = {}
    calls = 0
    for start in range(0, len(wanted), UUIDS_PER_CALL):
        found.update(by_uuid(wanted[start:start + UUIDS_PER_CALL]))
        calls += 1
    return {"bucket": bucket, "rows": rows, "found": found, "calls": calls}


def _write_refresh_stream(conn: psycopg.Connection, lead: dict[str, Any],
                          gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the fields the source published, then recompose and re-queue what changed.

    A station Radio Browser no longer returns gets **nothing**: no row, no placeholder, no
    "delisted" fact invented from an absence. It stays selected by the next run, which is
    the honest state — we asked and got no answer, which is not the same as an answer.
    """
    tenant_id = lead["tenant_id"]
    found: dict[str, dict[str, Any]] = prepared["found"]
    facts = recomposed = gone = unrebuildable = 0
    follow_on: list[dict[str, Any]] = []

    with conn.cursor() as cur:
        for row in prepared["rows"]:
            station = found.get(row["uuid"])
            if station is None:
                gone += 1
                continue
            party_id = row["party_id"]

            written: list[tuple[str, str, Any]] = []
            code = country_code(station)
            if code:
                written.append(("country_code", code, None))
            where = market(station)
            if where:
                written.append(("market", where, None))
            spoken = language(station)
            if spoken:
                written.append(("language", spoken, None))
            tags = radiobrowser.genres(station)
            if tags:
                written.append(("genre", ", ".join(tags), Json(tags)))

            for dimension, value, blob in written:
                cur.execute(
                    """INSERT INTO party_fact (tenant_id, party_id, dimension, value_text,
                                               value_json, provenance, status, source,
                                               written_by)
                       VALUES (%s, %s, %s, %s, %s, 'asserted', 'live', 'radio_browser',
                               'refresh_stream')
                       ON CONFLICT DO NOTHING""",
                    (tenant_id, party_id, dimension, value, blob),
                )
                facts += cur.rowcount

            # Recompose from the *whole* fact set, not from what this run added. The party
            # may carry a genre from Wikipedia and a licensee from the FCC, and composing
            # from a partial view would drop them out of the document.
            cur.execute(
                """SELECT dimension, value_text FROM party_fact
                    WHERE tenant_id = %s AND party_id = %s AND status = 'live'""",
                (tenant_id, party_id),
            )
            by_dimension = {f["dimension"]: f["value_text"] for f in cur.fetchall()}
            if not by_dimension.get("role", "").strip():
                # `profiles.from_facts` refuses a party with no `role`, and refusing is
                # correct — see 026. Counted and reported rather than caught and ignored,
                # because the fix is a migration a human applies, not a retry.
                unrebuildable += 1
                continue

            body = profiles.from_facts(row["name"], by_dimension)
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'profile_v2', '', %s, %s, %s, 'text/plain',
                           'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"], f"{row['name']} — profile", body,
                 # Party id in the hash, exactly as `ingest.recompose_profiles` argues:
                 # the terse composition collides across thousands of stations and a
                 # body-only hash would give the first one a document and the rest none.
                 hashlib.sha256(f"{party_id}:{body}".encode("utf-8")).hexdigest()),
            )
            if cur.rowcount == 0:
                # The composed text is byte-for-byte what this party already had, so its
                # vector would be identical. Re-embedding it would be a metered call that
                # cannot change a shortlist, and `spend.Gate` exists because those add up.
                continue
            recomposed += 1

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
                    "reason": "stream record refreshed, profile text changed",
                    "score": 0.6,
                })

    note = f", {unrebuildable} with no role fact" if unrebuildable else ""
    return fleet.Outcome(
        summary=(f"{prepared['bucket']}: {facts} fact(s) over {len(prepared['rows'])} "
                 f"station(s), {recomposed} profile(s) rewritten, {gone} no longer "
                 f"listed{note}"),
        facts=facts, documents=recomposed, leads=len(follow_on), follow_on=follow_on,
        calls=prepared["calls"],
    )


#: Re-read the Radio Browser entries we already hold and store the fields the first pass
#: dropped — above all `country_code`, without which no contact route may be written.
refresh_stream = fleet.NetworkAgent(fetch=_fetch_refresh_stream,
                                    write=_write_refresh_stream)
