"""The FCC's public licence register, as a source adapter.

This is the canonical home of the FM Query client. `platform/bin/fcc_nce_radio.py` wrote
it first as a standalone script that produced a JSON file; that file was a staging
artifact, and a staging artifact is not where a counterparty index belongs. The logic
moved here so the *fleet* can run it — `agents.index_stations` is the pipeline stage — and
the script now imports from this module rather than carrying a second copy.

**Why radio, and why this register.** `docs/research/10-creator-indexing.md` (Pillar 10 §4)
sets the standing rule: *no scraper, ever* — not chiefly on legal grounds but because
enforcement at our size is a ban on the accounts we need to run the campaign. Radio is the
channel where that rule costs nothing, because the underlying register is **public-domain
US government data**:

    https://transition.fcc.gov/fcc-bin/fmq       (FCC FM Query)

The query targets the **reserved noncommercial educational band, 88.1–91.9 MHz** — FM
channels 201 through 220, which Congress set aside for noncommercial educational
broadcasting. College and community radio live there almost by definition, and that single
frequency predicate does more filtering than any classifier could.

**What this gives, and what it does not.** The FCC knows who holds the licence, on what
frequency, from which city. It does **not** know the music director's name or address, and
no public dataset does. So this produces the *spine* — which stations exist, where, and who
runs them — and contact enrichment is a separate step that fills `contact_route` (migration
018). That split is deliberate: it is the difference between a fact the government
published and a guess we made.

**Provenance.** Everything transcribed from the feed is `measured` — it is a licence
record, not an estimate. Everything this module *derives*, above all `station_kind`, is
`inferred` and is labelled as such on every row. `SCOPE-RESET §2a` rule 1 forbids the
second from ever being mistaken for the first, and a classifier keying off a licensee's
name is exactly the kind of confident-looking guess that rule exists to contain.

Stdlib only, on purpose: this runs in the console Lambda's bundle as well as locally.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

FMQ = "https://transition.fcc.gov/fcc-bin/fmq"

#: The reserved noncommercial educational band. Channel 201 is 88.1 MHz and channel 220 is
#: 91.9. Kept as its own name because so much of this module's reasoning refers to it.
NCE_LOW, NCE_HIGH = 88.1, 91.9

#: The bands this module will query, and what each one is for.
#:
#: `nce` is college and community radio — the band Congress reserved, where a label with
#: an independent record has the best odds and the frequency predicate does the filtering.
#:
#: `commercial` is everything from 92.1 up. These are a different kind of counterparty and
#: it is worth being honest about that: a commercial station's music is largely chosen by
#: a corporate programming department against a chart, not by a music director reading
#: submissions. They belong in the index anyway — a regional independent, a specialty
#: show, a station that takes local artists — but a shortlist that ranks them the same way
#: it ranks a college station is a shortlist that will waste the label's time. Genre is
#: what separates them, which is why `index_streams` exists.
BANDS: dict[str, tuple[float, float]] = {
    "nce": (NCE_LOW, NCE_HIGH),
    "commercial": (92.1, 107.9),
    "all": (87.9, 107.9),
}

#: 50 states, DC, and the territories that hold FCC licences.
JURISDICTIONS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR", "VI", "GU", "AS", "MP",
]

#: The station kinds worth writing as counterparties. `other` is excluded and that is the
#: point of having it: a name this module could not read is a station somebody has to look
#: at, not one to mail. `network` and `religious` are excluded because their format has
#: nowhere to put a dance record.
PITCHABLE = ("college", "community", "public_radio")

# --------------------------------------------------------------------------- classifying
#
# Ordered most-specific first and evaluated in order, because the categories genuinely
# overlap: "EDUCATIONAL MEDIA FOUNDATION" contains both EDUCATIONAL and FOUNDATION and is
# neither a college nor a community station — it is K-LOVE, a national satellite feed.

#: National networks that hold hundreds of NCE licences each and originate no local music
#: programming. A translator carrying a satellite feed has no music director to pitch, so
#: including them would inflate the database with rows that can never become a lead. This
#: is the single most valuable filter in the file — EMF alone holds well over a thousand.
NETWORKS = [
    "EDUCATIONAL MEDIA FOUNDATION", "K-LOVE", "KLOVE", "AIR1", "AIR 1",
    "AMERICAN FAMILY ASSOCIATION", "AMERICAN FAMILY RADIO",
    "CALVARY CHAPEL", "CSN INTERNATIONAL", "CHRISTIAN SATELLITE",
    "MOODY BIBLE", "BIBLE BROADCASTING", "FAMILY STATIONS", "FAMILY RADIO",
    "RADIO TRAINING NETWORK", "HORIZON CHRISTIAN", "RELEVANT RADIO",
    "IMMACULATE HEART", "EWTN", "THREE ANGELS", "3ABN", "LIFETALK",
    "VERITAS CATHOLIC", "SALEM MEDIA", "WAY MEDIA", "GOOD NEWS RADIO",
    "PENFOLD COMMUNICATIONS", "CENTRO CRISTIANO", "WORLD RADIO NETWORK",
]

#: Higher education. `REGENTS`/`TRUSTEES`/`BOARD OF` catch the many licences held by a
#: governing body whose name never says "university".
COLLEGE = [
    "UNIVERSITY", "COLLEGE", "REGENTS", "TRUSTEES", "POLYTECHNIC",
    "INSTITUTE OF TECHNOLOGY", "STATE BOARD", "BOARD OF EDUCATION OF",
    "COMMUNITY COLLEGE", "SEMINARY", "STUDENT",
]

#: Public radio proper — an NPR member or a state network. Worth separating from community
#: radio because the pitch is different: these have formal music departments and rotation
#: meetings, where a volunteer community station has a person with a show.
PUBLIC = [
    "PUBLIC RADIO", "PUBLIC BROADCASTING", "PUBLIC TELECOMMUNICATIONS",
    "PUBLIC MEDIA", "EDUCATIONAL TELEVISION", "EDUCATIONAL BROADCASTING",
    "EDUCATIONAL TELECOMMUNICATIONS", "EDUCATIONAL RADIO",
    "BROADCASTING COMMISSION", "BROADCASTING AUTHORITY", "EDUCATIONAL COMMUNICATIONS",
]

#: K-12. Real licences, but a high-school station is rarely a useful counterparty for a
#: label, so it is separated rather than silently mixed into `college`.
SCHOOL = [
    "SCHOOL DISTRICT", "HIGH SCHOOL", "UNIFIED SCHOOL", "PUBLIC SCHOOLS",
    "ACADEMY", "PREPARATORY", "COUNTY SCHOOLS", "INDEPENDENT SCHOOL",
]

#: Locally-run religious broadcasters, as distinct from the national feeds in `NETWORKS`.
#: These are genuine local licensees with real staff, and they are still almost never a
#: counterparty for this label — the format is preaching, teaching or worship music, so a
#: dance record has nowhere to go. Separated rather than left in `other` because "we know
#: what this is and it is not for us" and "we could not tell" are different answers, and
#: collapsing them hides how much of the register has actually been identified.
#:
#: Ordered after `COLLEGE` on purpose: a Baptist university is a college with a radio
#: station, and the pitch there is the student music director, not the chaplain.
RELIGIOUS = [
    "CHRISTIAN", "MINISTRIES", "MINISTRY", "CHURCH", "BAPTIST", "FELLOWSHIP",
    "FAITH", "WORSHIP", "GOSPEL", "BIBLE", "CATHOLIC", "LUTHERAN", "EVANGEL",
    "CHAPEL", "MISSIONARY", "JESUS", "CHRIST", "PRAISE", "SALVATION ARMY",
    "PENTECOSTAL", "METHODIST", "PRESBYTERIAN", "ADVENTIST", "DIOCESE",
    "TABERNACLE", "SANCTUARY", "REDEEMER", "GRACE ", "CALVARY",
]

#: Everything left that looks like a locally-run nonprofit. Deliberately last.
COMMUNITY = [
    "FOUNDATION", "COMMUNITY", "FRIENDS OF", "COOPERATIVE", "ASSOCIATION",
    "SOCIETY", "ALLIANCE", "COUNCIL", "COALITION", "NEIGHBORHOOD", "CULTURAL",
    "ARTS", "CITIZENS", "VOLUNTEER",
]


def classify(licensee: str) -> str:
    """Guess what kind of station this is from its licensee name.

    **This is `inferred`, and the return value is never stored without that label.** It
    keys off a name, and names lie: a "COMMUNITY BROADCASTING FOUNDATION" may be a
    one-room volunteer station or a shell for a national feed. Order matters — `NETWORKS`
    is tested first precisely because those names collide with every other category.
    """
    name = licensee.upper()
    for needles, kind in ((NETWORKS, "network"), (COLLEGE, "college"),
                          (PUBLIC, "public_radio"), (SCHOOL, "school_k12"),
                          (RELIGIOUS, "religious"), (COMMUNITY, "community")):
        if any(n in name for n in needles):
            return kind
    return "other"


def pitchable(stations: list[dict[str, Any]], band: str) -> list[dict[str, Any]]:
    """Which of these stations are worth writing as counterparties, for this band.

    The two bands need different rules, and collapsing them would silently throw away
    most of the commercial register.

    In the **NCE** band, `classify` reads a licensee name that is usually a university, a
    foundation or a church, so its answer is informative and `PITCHABLE` is the filter:
    `other` is excluded on purpose — a name this module could not read is a station
    somebody has to look at, not one to mail.

    In the **commercial** band the same classifier is nearly blind: licensees are holding
    companies — "iHeartMedia Licenses, LLC", "Audacy License, LLC" — whose names say
    nothing about what the station plays, so almost everything lands in `other`. Filtering
    on `PITCHABLE` there would discard the entire commercial register for having corporate
    owners. So the only exclusion that survives is `network`, which is still exactly right:
    a translator carrying a satellite feed has no music director in either band.
    """
    if band == "nce":
        return [s for s in stations if s["station_kind"] in PITCHABLE]
    return [s for s in stations if s["station_kind"] != "network"]


def fetch(state: str, *, band: str = "nce", timeout: int = 90) -> str:
    """One jurisdiction's licensed FM stations in one band, as pipe-delimited text.

    `status=LIC` asks for current licences only. The query also returns construction
    permits and modification applications, which duplicate a station that is already
    licensed — `parse` drops them, and `dedupe` catches what survives.

    No `User-Agent` is set, which is deliberate. The service sits behind a filter that
    allowlists familiar client tokens and returns 403 to anything it does not recognise —
    `curl/8.5.0` and `Mozilla/5.0` pass, `respect-the-funk/1.0` does not. The two ways out
    are to send the default `Python-urllib/3.x`, which is what this client actually is, or
    to claim to be a browser. This sends the truth. If a future filter change blocks it
    too, ask the FCC for the bulk CDBS/LMS dumps rather than inventing a plausible string.
    """
    try:
        low, high = BANDS[band]
    except KeyError:
        raise ValueError(
            f"{band!r} is not a band; known: {', '.join(sorted(BANDS))}") from None

    query = urllib.parse.urlencode({
        "state": state, "freq": low, "fre2": high,
        "serv": "FM", "status": "LIC", "list": "4", "size": "9999",
    })
    request = urllib.request.Request(f"{FMQ}?{query}", headers={"Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def parse(text: str, state: str) -> list[dict[str, Any]]:
    """Pipe-delimited FM Query output into rows.

    Positions rather than names, because the feed has no header. They were read off a
    real row and are asserted here once, so a change in the feed's shape shows up as one
    wrong-looking value instead of silently shifting every field:

        0 call · 1 frequency · 8 status · 9 city · 10 state · 17 facility id · 26 licensee

    The licensee is at 26 and not, as it first appears, just past the state — fields 18–25
    are the transmitter's latitude and longitude, split one component per column. A row
    shorter than 27 fields is malformed and is skipped rather than read with a guess.

    A row whose licensee is blank is dropped rather than defaulted: the licensee is the
    only field this tool classifies on, and an empty one produces `other` for a station we
    know nothing about, which is worse than no row at all.
    """
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        f = [c.strip() for c in line.split("|")[1:]]
        if len(f) < 27 or f[8] != "LIC":
            continue
        call, licensee = f[0], f[26]
        if not call or not licensee:
            continue
        mhz = re.sub(r"[^0-9.]", "", f[1])
        out.append({
            "call_sign": call,
            "frequency_mhz": float(mhz) if mhz else None,
            "city": f[9].title(),
            "state": f[10] or state,
            "facility_id": f[17] or None,
            "licensee": licensee,
            # Everything above is transcribed from the licence record.
            "provenance": "measured",
            "source": "fcc_fm_query",
            # Everything below is this module's guess about it.
            "station_kind": classify(licensee),
            "station_kind_provenance": "inferred",
        })
    return out


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per station.

    A station appears more than once when it holds several authorisations — separate
    directional and non-directional records, or a licence alongside a modification. They
    are the same station and the same music director.

    Keyed on the FCC facility ID, which is the identifier the Commission itself uses for
    "this station" and survives a call-sign change. Call sign plus frequency is the
    fallback for the rare row with no facility ID, and it is a fallback rather than the
    key because two stations can share a call sign across services.
    """
    seen: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (("fac", row["facility_id"]) if row["facility_id"]
               else ("cf", row["call_sign"], row["frequency_mhz"]))
        seen.setdefault(key, row)
    return sorted(seen.values(), key=lambda r: (r["state"], r["call_sign"]))


def profile_text(station: dict[str, Any]) -> str:
    """What the embedding is computed from, and what a human would read to check it.

    Prose rather than a field dump, because this is the text R1 compares an artist's
    profile against: "who is this and what would they play" has to be *said* for cosine
    distance to mean anything. Every clause here is transcribed from the licence record
    except the station kind, which is named as a guess in the sentence itself so the
    hedge survives into anything quoting this text later.
    """
    call = station["call_sign"]
    mhz = station["frequency_mhz"]
    where = f"{station['city']}, {station['state']}"
    freq = f"{mhz:.1f} FM" if mhz else "FM"
    kind = station["station_kind"].replace("_", " ")

    return (
        f"{call} is a noncommercial educational radio station broadcasting on {freq} "
        f"from {where}, licensed to {station['licensee']}. It appears to be a {kind} "
        f"station, inferred from the licensee's name rather than confirmed. "
        f"Stations in the reserved 88.1–91.9 MHz band are noncommercial and educational "
        f"by licence condition, and college and community stations in this band "
        f"typically programme local and independent music, accept submissions from "
        f"unsigned and independent artists, and are run by student or volunteer music "
        f"directors."
    )
