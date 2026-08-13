"""Podcast Index — the shows that would play a record, and who presents them.

`fcc.py` and `radiobrowser.py` between them answer "which radio stations exist and what
do they play". Neither answers the question a label actually asks, which is *who decides*.
A station's music director is a person the licence register does not name and the stream
directory has never heard of, and finding them is a separate, manual step.

A podcast inverts that. The show **is** the person: the presenter's name is in the feed,
the show's own site is in the feed, and there is no programming department between a
pitch and the ear that hears it. Music podcasts — genre shows, DJ mixes, new-music
review, artist-interview shows — are a channel where an independent record has better
odds than commercial FM and where the counterparty is reachable by construction. They
belong in the same counterparty index as the stations, as the same kind of row: migration
009 argues at length that there is no `counterparty` table, and a podcast is another proof
of that argument rather than an exception to it.

**Podcast Index** (<https://podcastindex.org>) is the open, non-commercial index of the
podcast RSS ecosystem — roughly 4.4 million feeds — built explicitly so that podcasting
does not end up behind one company's API. It is free, it is keyed, and its terms do not
forbid what this module does. That matters against Pillar 10 §4's standing rule (*no
scraper, ever*): this is a published API being used as published, not a crawl of somebody
else's site with the user-agent filed off.


## Provenance, which here splits three ways rather than two

`SCOPE-RESET §2a` rule 1 says an inferred fact may never wear a measured one's clothes.
Applying it honestly to this source needs all three classes, because a single feed record
carries facts that were established in three genuinely different ways.

**`measured` — what Podcast Index's crawler observed.** `dead`, `lastHttpStatus`,
`episodeCount`, `newestItemPubdate`, `lastCrawlTime`. The index fetched the feed over
HTTP and counted what came back. That we did not personally make the measurement does not
demote it: `measured` is a claim about *how a fact was established*, not about who
established it — the same reasoning that lets `fcc.py` call a licence record `measured`
when nobody here has ever visited a transmitter.

**`asserted` — what the publisher wrote about themselves.** `title`, `author`,
`ownerName`, `description`, `categories`, `language`. Podcast Index did not derive any of
these; it copied them out of the show's own RSS. This is the same class Radio Browser's
tags get, and it is worth naming why it is the same class despite looking stronger. A
Radio Browser tag is a stranger's opinion of a station. A podcast category is the
*subject's own claim about itself* — and self-description is not more reliable than a
stranger's, it is differently unreliable. A show tags itself Music because Music is where
its listeners browse the App Store, which is a marketing decision, not a description of
programming. A talk show about the music industry and a show that plays records both land
in `Music`. Treating that as `measured` would let a shortlist rank a podcast on the
strength of its own advertising copy and never say so.

**`inferred` — what this module decides.** `show_kind` and `pitchable`. Both key off the
category list, which is the weakest input in the file, so both are labelled and neither
is ever promoted. `fcc.classify` is the precedent and carries the same warning.

**What is deliberately absent: an email address.** Podcast Index does not publish
`<itunes:owner><itunes:email>`, and this module does not manufacture one from the show's
domain. Migration 018 names a pattern guess (`music@<domain>`) as the single fastest way
to earn a spam complaint, and a source adapter that quietly invents a route is worse than
one that finds none — the second is a gap somebody can see. The show's website becomes a
`presence` row, which is a surface you may *read*; the route to send to comes from reading
it, later, as its own step.


## Why the host is a fact and not a second party

The goal this module was written against says "podcasts and their hosts". Only the first
half becomes a `party`, on purpose.

`ownerName` is a string — "Mary Anne Hobbs", "The Team". Creating a person-party from it
is an *identity* claim: that this Mary Anne Hobbs is or is not the one on that other feed,
and on that Deezer profile. That is precisely the guess `suggestion` exists to hold behind
a human, and precisely the guess that produced thirteen junk parties from a name-match
harvest in August. So the host's name is written as a `party_fact` on the show, in the
same place and the same shape as a station's `licensee`, and the person becomes a
`contact_route.addressee` when there is a route to attach them to — which is exactly the
split 018 was written to make.


## Credentials, and why their absence raises

Every call needs four headers, one of which is `sha1(apiKey + apiSecret + unixTime)`.
There is no anonymous tier and no read-only mirror: without a key this module can do
nothing at all, so `credentials()` raises rather than returning empty strings for a caller
to discover halfway down a stack trace. `SCOPE-RESET`'s no-silent-fallback rule is not
decoration here — an adapter that skipped quietly would leave the console showing an
`index_podcasts` stage that runs, succeeds, and writes nothing, which looks exactly like a
corpus with no music podcasts in it.

The `X-Auth-Date` timestamp is checked against the server's clock with a **three-minute
tolerance**, so a machine with a skewed clock and a perfectly good key gets a 401. That is
a real hour of somebody's life, so `Refused` says so in the message rather than leaving
"401 Unauthorized" to be interpreted.


## Paging, and why it is feed-ID blocks

Two endpoints could enumerate this index and only one of them is safe to build on.

`/recent/feeds` takes a `cat` filter, which is tempting because it would fetch only the
music categories. It has no cursor — it pages by walking a `since` timestamp backwards —
so resuming after a crash means reconstructing where the walk had got to, and a gap in
that walk is invisible: you get fewer rows and no error.

`/recent/newfeeds` takes `feedid`, "the PodcastIndex Feed ID to start from". Feed IDs are
integers assigned in order and never reused, so they tile: block *k* is the half-open
range `[k·BLOCK, (k+1)·BLOCK)`, every ID is in exactly one block, and a block is
addressable, deterministic and re-runnable without reference to any other. It costs a
category filter — `/recent/newfeeds` has none, so the filtering happens here, over every
feed rather than over the music ones. That is roughly 15,000 calls to reach 85,000 shows,
about six useful rows per call, and buying idempotency with bandwidth is the right
direction for a stage that will be interrupted.

**`BLOCK` is smaller than the API's own cap, and that gap is load-bearing.** The endpoint
returns at most 1,000 rows and does not say whether it truncated. If a block were also
1,000 IDs wide, a full response would be indistinguishable from a clipped one and the
stage would silently lose the tail of dense regions — the failure this codebase keeps
calling the worst kind, because it reports success. A 500-ID block cannot contain more
than 500 feeds, so a 1,000-row response is proof the block is complete. Rows outside the
block are dropped here and collected by the block that owns them.

Stdlib only, like `fcc.py` and `radiobrowser.py`, so it fits in the console Lambda's
bundle without a dependency the zip has to carry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from rtf_platform import profiles

BASE = "https://api.podcastindex.org/api/1.0"

#: The project's name, not a browser's — same rule as `fcc.fetch` and `radiobrowser`.
#: Podcast Index asks callers to identify themselves and this obliges.
USER_AGENT = "respect-the-funk/1.0 (+counterparty index)"

#: Where the credentials come from. Named for the service rather than the platform,
#: matching `SPOTIFY_CLIENT_ID` / `OPENAI_API_KEY` — `PLATFORM_*` is reserved for this
#: deployment's own configuration, and a third party's key is not that.
KEY_VAR = "PODCASTINDEX_API_KEY"
SECRET_VAR = "PODCASTINDEX_API_SECRET"

#: Feed IDs one lead covers. See the module docstring: this is deliberately half the
#: API's row cap so that a full response can never be mistaken for a truncated one.
BLOCK = 500

#: What one call asks for. The API's documented maximum, and twice `BLOCK`, so a block is
#: always fetched whole in a single round trip.
PAGE = 1000

#: The Podcast Index taxonomy is Apple's, flattened: a show in Apple's "Music Commentary"
#: carries the two categories `Music` and `Commentary`, because `Commentary` is a leaf
#: shared with other parents. So the *parent* is what identifies a music show, and the
#: leaf only refines it. Matching on the parent rather than enumerating leaf names is
#: deliberate — the leaf set is not something this module can verify without a key, and a
#: hardcoded list that silently missed one would drop a whole genre of show.
MUSIC = "music"

#: Leaves that say what *kind* of music show this is, and how much of a pitch target it
#: is. A show that plays records is the target; a show that talks about the industry is a
#: press opportunity and a waste of a promo. Keeping the distinction rather than
#: collapsing it is the same argument `radiobrowser.NON_MUSICAL` makes.
SHOW_KINDS = {
    "commentary": "music_commentary",
    "history": "music_history",
    "interviews": "music_interviews",
    "reviews": "music_reviews",
}

#: The `medium` values a pitchable show can have. `music` is excluded and that is not a
#: typo: Podcast Index's `music` medium means the feed *is* an album published over RSS,
#: so its "host" is the artist. Those are peers, not counterparties, and mailing one a
#: promo is mailing a stranger their own competition.
MEDIA = frozenset({"podcast", "audiobook", "", "video", "film"})

#: How much of a show's own blurb reaches the embedding. Podcast Index truncates
#: `description` to about 100 words unless `fulltext` is asked for, and `fulltext` is
#: deliberately never asked for: show notes run to thousands of words of episode lists and
#: "subscribe on Apple Podcasts", and a vector computed over that is a vector of
#: boilerplate. This is a second ceiling for the feeds that arrive long anyway.
BLURB_CHARS = 600


class NotConfigured(RuntimeError):
    """No API key and secret. Nothing can be fetched at all.

    Shaped after `mail.MailNotConfigured` for the same reason it exists: there is no
    degraded mode worth having. An adapter that returned an empty page here would report
    success and write nothing, and the console would show a healthy stage indexing a
    world with no podcasts in it.
    """


class Refused(RuntimeError):
    """The API answered, and the answer was no.

    `permanent` carries whether retrying could ever work, exactly as `mail.MailRefused`
    does, because the two failures need opposite handling: a 429 is the service asking us
    to slow down and belongs back on the frontier with a backoff, while a 401 is a wrong
    key and four more attempts only delay somebody reading the message.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


# --------------------------------------------------------------------------- credentials

def credentials() -> tuple[str, str]:
    """The key and secret, or a message saying exactly how to get them.

    Both or neither: a key with no secret cannot sign a request, so accepting one and
    proceeding would produce a 401 whose cause is two layers away from its symptom.
    """
    key = os.environ.get(KEY_VAR, "").strip()
    secret = os.environ.get(SECRET_VAR, "").strip()
    if not (key and secret):
        missing = ", ".join(name for name, value in
                            ((KEY_VAR, key), (SECRET_VAR, secret)) if not value)
        raise NotConfigured(
            f"Podcast Index needs {KEY_VAR} and {SECRET_VAR}; {missing} "
            f"{'is' if missing.count(',') == 0 else 'are'} not set. Both are free and "
            "issued immediately at https://api.podcastindex.org/signup — the key looks "
            "like 'ABCDEFGH1234567890AB' and the secret is the longer string shown "
            "beside it. Set them in the worker's environment (and in the console "
            "Lambda's, if the stage is to run there).")
    return key, secret


def signature(key: str, secret: str, at: int) -> str:
    """The `Authorization` header: `sha1(key + secret + unix_seconds)`, lowercase hex.

    SHA-1 is not a choice made here and is not being relied on for anything: it is the
    scheme the service publishes, and the value it protects is a request that reads public
    data. Substituting a stronger hash would simply fail to authenticate.
    """
    return hashlib.sha1(f"{key}{secret}{at}".encode("utf-8")).hexdigest()


def headers(key: str, secret: str, at: int) -> dict[str, str]:
    """The four headers every call carries.

    `at` is passed in rather than read from the clock inside, so the signature is a pure
    function of its inputs and a test can assert the exact hex for a known triple. The
    server allows three minutes of skew either side of its own clock; see the module
    docstring for why that is the first thing a 401 message should mention.
    """
    return {
        "User-Agent": USER_AGENT,
        "X-Auth-Key": key,
        "X-Auth-Date": str(at),
        "Authorization": signature(key, secret, at),
        "Accept": "application/json",
    }


# ------------------------------------------------------------------------------ fetching

def _get(path: str, params: dict[str, Any] | None = None, *,
         timeout: int = 60) -> dict[str, Any]:
    """One authenticated GET, or a `Refused` that says which kind of no this was.

    Every branch below raises. There is no path through this function that returns a
    partial or empty result to a caller who cannot tell the difference — an adapter whose
    error handling degrades into `{}` is an adapter that turns an outage into an empty
    corpus.
    """
    key, secret = credentials()
    at = int(time.time())
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(url, headers=headers(key, secret, at))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise Refused(
                f"Podcast Index rejected the credentials in {KEY_VAR}/{SECRET_VAR} "
                f"(401). Two things produce this and only one of them is the key: the "
                f"request was signed for unix time {at}, and the service refuses any "
                f"timestamp more than three minutes from its own clock. Check the "
                f"machine's clock before regenerating the key. Response: {detail}",
                permanent=True) from exc
        if exc.code == 429:
            raise Refused(
                "Podcast Index is rate-limiting this client (429). The lead goes back "
                "to the frontier on its backoff; if it recurs, lower the stage's "
                f"batch_size rather than retrying harder. Response: {detail}",
                permanent=False) from exc
        raise Refused(
            f"Podcast Index answered {exc.code} for {path}: {detail}",
            permanent=400 <= exc.code < 500) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(
            f"Podcast Index returned {len(raw)} bytes for {path} that are not JSON. "
            f"First 200: {raw[:200]!r}", permanent=False) from exc

    if not isinstance(body, dict):
        raise Refused(f"Podcast Index returned {type(body).__name__} for {path}, "
                      "not the documented object", permanent=False)

    # The envelope carries its own verdict, as `"true"` or `true` depending on endpoint.
    # A body whose `status` is absent is not assumed good: this codebase has been bitten
    # by treating a shape it did not recognise as a success.
    status = body.get("status")
    if status not in (True, "true", "True"):
        raise Refused(
            f"Podcast Index reported failure for {path}: status={status!r} "
            f"description={body.get('description')!r}", permanent=False)
    return body


def newest_feed_id(*, timeout: int = 60) -> int:
    """The highest feed ID the index has issued, read from the index itself.

    This is what bounds the seeding, and it is *asked for* rather than hardcoded — the
    same discipline as `ingest.index_streams` deriving its page count from Radio Browser's
    own country counts. A constant here would silently stop covering the tail of the
    index the week after it was written.

    `/recent/newfeeds` returns newest-first, so one row is enough.
    """
    body = _get("/recent/newfeeds", {"max": 1}, timeout=timeout)
    feeds = body.get("feeds") or []
    if not feeds:
        raise Refused("Podcast Index returned no feeds for /recent/newfeeds?max=1, so "
                      "there is no ceiling to page to. This is not a normal state for "
                      "an index of four million feeds; check the endpoint before "
                      "assuming the answer is zero.", permanent=False)
    top = feeds[0].get("id")
    if not isinstance(top, int) or top <= 0:
        raise Refused(f"the newest feed has id {top!r}, which is not an ID this module "
                      "can page from", permanent=False)
    return top


def block(start: int, *, span: int = BLOCK, timeout: int = 60) -> list[dict[str, Any]]:
    """Every feed whose ID falls in `[start, start + span)`.

    The half-open range is enforced **here**, on the rows, rather than trusted from the
    request. `feedid` is documented as "the ID to start from" and the documentation does
    not say whether that is inclusive; filtering client-side makes the answer the same
    either way, and — more importantly — makes the blocks tile exactly, with no row
    covered twice and none covered by nobody.

    Asking for `PAGE` rows to fill a `span` of half that is not waste; it is the proof
    that the block came back whole. See the module docstring.
    """
    if start < 0 or span <= 0:
        raise ValueError(f"a block starts at or after 0 and spans at least 1; "
                         f"got start={start}, span={span}")
    body = _get("/recent/newfeeds", {"feedid": start, "max": PAGE}, timeout=timeout)
    feeds = body.get("feeds")
    if feeds is None:
        raise Refused("Podcast Index answered without a `feeds` key, so this block "
                      "cannot be told apart from an empty one", permanent=False)
    end = start + span
    return [f for f in feeds
            if isinstance(f.get("id"), int) and start <= f["id"] < end]


def blocks_to(ceiling: int, *, start: int = 0, span: int = BLOCK) -> list[int]:
    """The starting ID of every block needed to cover `[start, ceiling]`.

    Pure arithmetic, deliberately: the seeder must be able to enumerate the whole unit of
    work without a network call per block, and a block that turns out to be empty costs
    one request and writes nothing rather than needing to have been predicted.
    """
    if ceiling < 0:
        raise ValueError(f"a feed ID ceiling is not negative; got {ceiling}")
    return list(range(max(0, start), ceiling + 1, span))


# ------------------------------------------------------------------------------- reading

def categories(feed: dict[str, Any]) -> list[str]:
    """The show's categories, lowercased, deduplicated, order preserved.

    Podcast Index returns these as an object keyed by category ID (`{"55": "Music",
    "77": "Commentary"}`), and the IDs are not stable enough across the taxonomy's
    revisions to key on — the names are what the API's own `cat` filter matches. Order is
    preserved because the publisher's first category is the one they consider primary.

    A feed with no categories gets an empty list and not a guess. `profiles.UNKNOWN`
    exists precisely so that "we do not know what this plays" can be said in words that
    rank last rather than plausibly.
    """
    raw = feed.get("categories")
    if not isinstance(raw, dict):
        return []
    out: list[str] = []
    for name in raw.values():
        text = str(name or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def musical(cats: list[str]) -> bool:
    """Is this show filed under music at all?

    The parent category, not a leaf. See `MUSIC`.
    """
    return MUSIC in cats


def show_kind(cats: list[str]) -> str:
    """What kind of music show this appears to be — **`inferred`, always labelled.**

    Keyed off the leaf category, which the publisher chose about themselves, so this
    inherits every weakness of `fcc.classify` plus one of its own: a show can be filed
    under `Music` and `Commentary` and still be three hours of unbroken DJ mix. The
    return value goes into `party_fact` with `provenance = 'inferred'` and is never
    promoted, and `profiles.compose` renders it as a hedge in the sentence itself.

    `music_show` is the answer for a music feed with no refining leaf, and it is the most
    valuable of the four: a show filed under Music and nothing else is more likely to be
    a show that plays records than one that has told the store it is commentary.
    """
    if not musical(cats):
        return "other"
    for leaf in cats:
        if leaf in SHOW_KINDS:
            return SHOW_KINDS[leaf]
    return "music_show"


def host(feed: dict[str, Any]) -> str:
    """Who presents the show, as the feed says.

    `ownerName` first because `<itunes:owner>` is the field the format reserves for the
    responsible human, and `author` second because plenty of feeds fill in only one. Both
    are `asserted`; neither becomes a party. See the module docstring.
    """
    for field in ("ownerName", "author"):
        name = str(feed.get(field) or "").strip()
        if name:
            return name[:200]
    return ""


def alive(feed: dict[str, Any]) -> bool:
    """Is this show still publishing?

    Three `measured` signals, all from Podcast Index's own crawler. `dead` is its verdict,
    `lastHttpStatus` is what the feed last answered, and `episodeCount` of zero is a feed
    that parses but has nothing in it. A counterparty index full of shows that stopped in
    2019 is one nobody trusts, which is the same argument `radiobrowser.stations` makes
    with `hidebroken`.
    """
    if int(feed.get("dead") or 0):
        return False
    if int(feed.get("episodeCount") or 0) <= 0:
        return False
    status = feed.get("lastHttpStatus")
    # A feed never yet crawled has no status. That is unknown, not bad, and treating it
    # as bad would drop every feed added in the last few hours.
    return status is None or 200 <= int(status) < 400


def pitchable(feed: dict[str, Any]) -> bool:
    """Would it make sense to send this show a record? **`inferred`.**

    Everything this predicate rests on is somebody else's assertion about themselves, so
    a `True` here means "worth a look", never "worth a send" — the send is gated on a
    `contact_route`, which this source cannot supply.
    """
    if not alive(feed):
        return False
    if str(feed.get("medium") or "").strip().lower() not in MEDIA:
        return False
    return musical(categories(feed))


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def blurb(feed: dict[str, Any]) -> str:
    """The show's own description, stripped of markup and capped.

    Podcast descriptions arrive as whatever the publisher's CMS emitted — HTML, entities,
    hard line breaks. The tags carry no meaning to an embedding and a great deal of
    length, so they go; the text stays, because it is the single best signal this source
    has. A station has four genre words and a podcast has a paragraph about what it plays.
    """
    text = _TAG.sub(" ", str(feed.get("description") or ""))
    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
    text = _SPACE.sub(" ", text).strip()
    return text[:BLURB_CHARS].rstrip()


def profile_text(feed: dict[str, Any]) -> str:
    """What the embedding is computed from.

    The structured half is `profiles.compose`, not a fourth composer — `profiles.py` was
    written because three modules were each writing their own and half of every document
    was a sentence every other document also had. `role="podcast"` is why that function
    takes a role at all.

    The blurb goes in through `compose`'s `prose` slot rather than being appended after
    it. An earlier version of this function did append, and said so — `compose` had no
    slot for free text because no source before this one had any — which made this module
    the fourth composer that `profiles.py` was written to prevent. It is now the third
    caller of the one composer and nothing more: every rule about what reaches a vector,
    including how much of a long description does and what is stripped out of it, is
    argued and applied in one file.

    Rule 2 of `profiles.py` still holds, and now holds against the blurb too. The show's
    *name* is not composed in — `party.name` carries it for display, and putting it in
    the vector is how an artist called Deep House gets shortlisted to a show called Deep
    House. The title and the host are passed as `names` so that the same rule reaches
    inside the publisher's own prose, where "Deep House Weekly is a weekly…" would
    otherwise put it back.
    """
    cats = categories(feed)
    kind = show_kind(cats)
    # `music_show` is withheld from the prose even though it is written as a fact.
    # `compose` renders a kind as "a <kind> podcast", and "a music show podcast" is a
    # phrase no human wrote and no query will match; the genre list already carries the
    # same claim in words the embedding can use. The fact keeps the distinction for
    # anyone querying `party_fact` — which is where a distinction belongs when the prose
    # cannot say it well.
    return profiles.compose(
        genres=cats,
        station_kind="" if kind in {"other", "music_show"} else kind,
        language=str(feed.get("language") or "").strip(),
        role="podcast",
        prose=blurb(feed),
        # The two identity strings this source knows. `title` is the show's own name and
        # `host` is a person's; both are written as facts elsewhere, and neither belongs
        # in the vector. `profiles` decides what to do with them — this module only says
        # which strings they are.
        names=[str(feed.get("title") or ""), host(feed)],
    )
