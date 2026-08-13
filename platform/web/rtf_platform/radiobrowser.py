"""Radio Browser — what a station actually plays, and where its website is.

The FCC register (`fcc.py`) answers *which stations exist* with authority, and answers
*what they play* not at all. That gap is the one that matters to a label: a shortlist that
cannot tell a classical station from a hip-hop station will happily take a trap single to
a symphony broadcaster, and every counterparty it returns will be a plausible-looking
waste of the label's time.

**Radio Browser** (<https://api.radio-browser.info>) is a community-maintained, openly
licensed index of streaming radio — no key, no quota, explicitly free to use. It carries
the two fields the licence register cannot:

  * **tags** — genre and format, as the community describes the station
  * **homepage** — the station's own site, which is the route to a real contact

Measured 2026-08-11 against the live API: **7,709 US stations, 5,335 with tags, 7,544 with
a homepage.** Of the 1,777 NCE stations already indexed from the FCC, **373 appear here by
call sign** — so this is both an *enrichment* of stations we know and a *source* of
thousands we do not.

**Provenance: `asserted`, and this is the interesting call.** An FCC licensee is
`measured` — transcribed from the government's own record. A Radio Browser tag is not
that. It is also not `inferred`, because this module derives nothing: it copies a string
somebody else wrote. `asserted` is the honest class — *a human said so* — and it is the
right one precisely because it is weaker than `measured`. A shortlist that ranks a station
because a stranger typed "jazz" into a community database should be able to see that is
what happened. `SCOPE-RESET §2a` rule 1 is about never letting a guess wear a measurement's
clothes; this is the same rule applied to somebody else's guess.

Stdlib only, like `fcc.py`, so it runs in the console Lambda's bundle.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

#: One of the round-robin mirrors. Radio Browser asks clients to discover a server via
#: DNS SRV and to send a real User-Agent so operators can see who is calling; both are
#: honoured here. The name is the project's, not a browser's — same rule as `fcc.fetch`.
BASE = "https://de1.api.radio-browser.info"
USER_AGENT = "respect-the-funk/1.0 (+counterparty index)"

#: How many stations one lead writes. Radio Browser will return a whole country in one
#: response — 7,709 rows for the US — and writing that in a single transaction would hold
#: one lock for the length of the whole country. A page is the unit of work, exactly as a
#: jurisdiction is for `fcc.py`.
#:
#: **100, measured rather than chosen.** The first version used 500 and no page finished
#: inside a 500-second timeout: each station can touch six tables, and this cluster is a
#: CockroachDB BASIC deployment scaled to zero, whose request-unit budget is the real
#: constraint — not the schema and not the network. A page that cannot commit inside its
#: lease is worse than a small one, because the lease expires mid-write and a second
#: worker redoes all of it. Raise this only against a measurement, never against a guess.
PAGE = 100

#: Tags that describe a *format* rather than music, and are worth keeping for exactly that
#: reason: a station tagged only these plays no music a label can place, and a shortlist
#: should be able to see that rather than scoring it on silence.
NON_MUSICAL = frozenset({
    "talk", "news", "sports", "weather", "traffic", "public radio", "npr",
    "religious", "christian", "gospel talk", "preaching", "politics",
})


def _get(path: str, params: dict[str, Any] | None = None, *, timeout: int = 90) -> Any:
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def countries() -> list[dict[str, Any]]:
    """Every country Radio Browser knows, with how many stations it holds.

    This is what the seeder pages against: asking for the count first means the number of
    leads is derived from the source rather than guessed, so a country that grows does not
    silently lose its tail to a hardcoded page count.
    """
    return _get("/json/countries")


def stations(country_code: str, *, offset: int = 0, limit: int = PAGE,
             timeout: int = 90) -> list[dict[str, Any]]:
    """One page of a country's stations, newest-checked first.

    `hidebroken` drops streams that failed their last check. A dead stream is not proof
    the station is gone — plenty of real stations stream badly — but it is a strong signal
    the row is stale, and a counterparty index full of stale rows is one nobody trusts.
    """
    return _get("/json/stations/search", {
        "countrycode": country_code.upper(), "offset": offset, "limit": limit,
        "hidebroken": "true", "order": "clickcount", "reverse": "true",
    }, timeout=timeout)


#: US and Canadian broadcast call signs: K or W (US), C (Canada), three or four letters.
#: Anchored on a word boundary so "WREK Atlanta 91.1" matches and "NETWORK" does not.
CALL_SIGN = re.compile(r"\b([KWC][A-Z]{2,3})\b")


def call_sign(name: str) -> str | None:
    """The call sign in a station's display name, if there is one.

    Radio Browser names are free text — "WREK Atlanta 91.1", "Hard Rock Radio FM",
    "KEXP 90.3 FM". Roughly a fifth of US rows lead with a call sign, and that fifth is
    what lets this module *enrich* a station the FCC already gave us instead of creating
    a second party for it. A name with no call sign is not a failure; it is a station we
    have no other record of, and it becomes a new counterparty.

    Returns the first match only. A name containing two call signs is a simulcast, and
    guessing which of the two the row is about is exactly the kind of confident guess
    `SCOPE-RESET §2a` forbids — the first is the one the name leads with.
    """
    found = CALL_SIGN.search((name or "").upper())
    return found.group(1) if found else None


def genres(station: dict[str, Any]) -> list[str]:
    """The station's tags, cleaned, deduplicated, order preserved.

    Lowercased because the community is inconsistent about case and a shortlist that
    treats "Jazz" and "jazz" as different genres has two half-populated buckets instead of
    one useful one. Order is preserved because Radio Browser's own ordering puts the tag
    the submitter thought most important first.
    """
    raw = (station.get("tags") or "").split(",")
    seen: list[str] = []
    for tag in raw:
        tag = tag.strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def musical(tags: list[str]) -> bool:
    """Does this station appear to play music at all?

    A station tagged only `talk, news, npr` is a real counterparty for a press campaign
    and a waste of a pitch for a single. Returning the answer rather than filtering on it
    keeps the decision at the call site, where the campaign's channel is known.
    """
    return any(t not in NON_MUSICAL for t in tags)


def profile_text(station: dict[str, Any], tags: list[str]) -> str:
    """What the embedding is computed from — and the whole reason this module exists.

    The FCC profile says a station is noncommercial and educational and leaves what it
    plays to the imagination. That text embeds to roughly the same vector for every
    station in the register, which is why a shortlist built on it ranks a classical
    broadcaster and a hip-hop show as equally good homes for a trap single.

    Genre goes in the *prose*, not just in a column, because prose is what gets embedded.
    The tags are repeated as a plain list as well: cosine similarity over a sentence is
    dominated by the sentence's shape, and a bare list of genre words gives the vector
    something blunt to hold on to.
    """
    name = station.get("name", "").strip()
    where = ", ".join(x for x in (station.get("state"), station.get("country")) if x)
    language = station.get("language") or ""
    plays = ", ".join(tags) if tags else "an unstated mix of programming"

    text = f"{name} is a radio station"
    if where:
        text += f" broadcasting from {where}"
    text += f". It plays {plays}."
    if language:
        text += f" It broadcasts in {language}."
    if tags:
        text += f" Genres and formats: {plays}."
        if not musical(tags):
            text += (" This station's tags describe speech programming rather than "
                     "music, so it is unlikely to place a record.")
    text += (" Tags are contributed by the Radio Browser community and are asserted "
             "rather than measured.")
    return text
