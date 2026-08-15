"""Station formats, read out of Wikipedia's infoboxes.

The third genre source, and the one that actually closes the gap. Measured 2026-08-11
against the 8,076 indexed call signs that had no genre from any other source:

    Radio Browser tags   — strong where it reaches, reaches ~a third of the index
    Wikidata (P415)      — 13.7% of the remainder
    Wikipedia infoboxes  — **85%** of a random 20-station sample of the remainder

The reason for the gap is structural rather than accidental. Radio Browser only knows
stations that stream, Wikidata only knows what somebody has curated into structured form,
but virtually every licensed US broadcast station has a Wikipedia article, because the
article is how the community documents a licence. The `format =` line in
`{{Infobox radio station}}` is the field this reads and nothing else.

**Provenance is `asserted`, exactly as for Radio Browser.** It is a human-written
encyclopaedia field, not a measurement we took and not an inference we drew.
`SCOPE-RESET §2a` rule 1 is about never letting somebody else's claim wear a
measurement's clothes, and a shortlist that ranks a station on this should be able to see
that an editor typed it.

**Where a format cannot be found, nothing is written.** No default, no "unknown" genre
that later reads as a real one, no guess from the licensee's name. A station with no
published format is a station whose format we do not know, and the shortlist is entitled
to see the absence rather than a confident-looking filler — which is the same discipline
`harvested.py` enforces at the parse boundary.

Wikipedia's API is open and documented. This sends a descriptive `User-Agent` with a
contact address as their bot policy asks, batches titles so one request answers for many
stations, and passes `maxlag` so a lagging replica tells us to back off instead of being
hammered.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = ("respect-the-funk/1.0 (radio counterparty index; "
              "contact anthonybtedesco@gmail.com)")

#: Titles per request. The API accepts 50, but `rvprop=content` returns the whole
#: wikitext of every one of them — a station article runs 20–40 KB, so 50 titles is a
#: two-megabyte response. 25 keeps a request under a megabyte and still means one call
#: answers for twenty-five stations.
BATCH = 25

#: `| format = Classic rock` in `{{Infobox radio station}}`. Stops at a newline or the
#: next pipe so a one-line infobox does not swallow the rest of the template.
FORMAT = re.compile(r"\|\s*format\s*=\s*([^\n|]+)", re.IGNORECASE)

#: Wiki markup that carries no meaning once the value is a plain string: links, template
#: braces, bold/italic ticks, and the disambiguating parenthetical Wikipedia appends to
#: format articles — "Full service (radio format)" is the format "full service".
_MARKUP = re.compile(r"\[\[|\]\]|\{\{|\}\}|'{2,}")
_DISAMBIG = re.compile(r"\s*\((?:radio\s+)?format\)\s*$", re.IGNORECASE)


def _clean(raw: str) -> str:
    """One infobox value into a genre string, or '' if nothing survives.

    `[[Classic rock]]` becomes `classic rock`; `[[Contemporary hit radio|CHR]]` becomes
    `contemporary hit radio`, taking the *target* rather than the display text because
    the target is the canonical name and the display text is often an initialism that
    embeds badly. Lowercased for the same reason `radiobrowser.genres` lowercases: two
    spellings of one genre is two half-populated buckets.
    """
    value = _MARKUP.sub("", raw)
    # A piped link keeps the target, which is the half before the pipe once the brackets
    # are gone. Done after markup stripping so the pipe that survives is the link's own.
    value = value.split("|")[0]
    value = _DISAMBIG.sub("", value)
    value = re.sub(r"<[^>]+>", "", value)          # <ref>…</ref> leftovers
    value = re.sub(r"\s+", " ", value).strip(" .,;")
    return value.lower().strip()


def _request(titles: list[str], *, timeout: int = 60) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content",
        "rvslots": "main", "titles": "|".join(titles), "format": "json",
        "redirects": "1", "maxlag": "5",
    })
    request = urllib.request.Request(f"{API}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _pages(payload: dict[str, Any]) -> dict[str, str]:
    """Title -> wikitext, following the redirects the API resolved for us.

    `normalized` and `redirects` are how the API reports that "WXYZ-FM" answered from
    "WXYZ (FM)". Both are followed so the caller gets its own title back as the key and
    never has to know which spelling Wikipedia settled on.
    """
    query = payload.get("query", {})
    alias: dict[str, str] = {}
    for hop in ("normalized", "redirects"):
        for entry in query.get(hop, []):
            alias[entry["to"]] = alias.get(entry["from"], entry["from"])

    out: dict[str, str] = {}
    for page in query.get("pages", {}).values():
        revisions = page.get("revisions")
        if not revisions:
            continue
        title = page.get("title", "")
        text = revisions[0].get("slots", {}).get("main", {}).get("*", "")
        out[alias.get(title, title)] = text
    return out


def formats(call_signs: list[str], *, timeout: int = 60) -> dict[str, str]:
    """Call sign -> format, for those Wikipedia documents.

    Two passes, because station articles live under three spellings and there is no way
    to know which without asking. The bare call sign is tried first since it is the most
    common, then `X (FM)` for the ones that missed. A call sign that misses both is
    absent from the result — the caller writes nothing for it.
    """
    found: dict[str, str] = {}
    remaining = [c for c in call_signs if c]

    for variant in ("{}", "{} (FM)"):
        pending = [c for c in remaining if c not in found]
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            titles = [variant.format(c) for c in chunk]
            try:
                pages = _pages(_request(titles, timeout=timeout))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                # One batch failing must not lose the others. The lead stays incomplete
                # and its next run picks up whatever is still missing, because the
                # caller selects on "has no genre yet" rather than on a cursor.
                continue
            for call, title in zip(chunk, titles):
                text = pages.get(title)
                if not text:
                    continue
                match = FORMAT.search(text)
                if not match:
                    continue
                value = _clean(match.group(1))
                if value:
                    found[call] = value
    return found
