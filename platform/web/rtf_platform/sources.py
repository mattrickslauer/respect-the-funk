"""Source adapters. Given a surface we know about, go and get what is behind it.

`source_manifest` already describes six sources — what each accepts, what it emits, what
it yields, whether it needs a key, how fast it may be called and how often it is worth
re-checking. Every row is `enabled = false` with a `disabled_reason`, because until this
module existed none of them could be true. This is the code those rows describe.

## The loop this is half of

    operator asserts a Spotify URL for an artist
        -> presence row written, and a `map_source` lead written with it
            -> an agent claims the lead and calls the adapter here
                -> identifiers, facts, metrics, recordings, releases are written
                -> and follow-on leads are written for what was discovered
                    -> which are claimed in turn, until depth or budget stops it

Nothing in that chain calls anything else in it. Each step writes rows; the rows are the
call. `lead.parent_lead_id` and `lead.depth` record the shape of the expansion so it can
be read back, and `party_budget.max_depth` is what stops it being infinite.

## Enabled is data, not code

An adapter existing does not make it run. `source_manifest.enabled` does, and it lives in
the database precisely so turning a source on is an operational act rather than a deploy.
`disabled_reason` is carried alongside so the console can say *why* a source is dark
instead of just showing it greyed out — the same discipline `presence.state` uses for
`absent`, where "we asked and it is not there" and "we never asked" must not look alike.

## What is measured, what is inferred, what is asserted

The provenance rule from `SCOPE-RESET §2a` is enforced here rather than left to callers:

  * a follower count read from a platform's own API is **measured**
  * a genre tag the platform assigned, or a name match we made ourselves, is **inferred**
  * the operator pasting the URL in the first place is **asserted**

The consequence that matters: an adapter that *searches* for an artist by name has
inferred the match, and an inferred match may not become an asserted `presence` row on
its own. It becomes a `suggestion` for a human to accept. Auto-accepting a name match is
how a roster ends up confidently pointing at the wrong "Amanda Kurt".
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

USER_AGENT = "respect-the-funk/0.1 (+https://github.com/mattrickslauer/respect-the-funk)"


class SourceUnavailable(RuntimeError):
    """The adapter cannot run — missing credentials, or the provider is refusing."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Harvest:
    """Everything one fetch produced, as rows-to-be rather than as writes.

    The adapter does not touch the database. It returns this, and the agent writes it in
    one transaction — so a partial fetch cannot leave a half-mapped artist, and an
    adapter can be tested without a cluster.
    """

    identifiers: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    recordings: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    leads: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    summary: str = ""


def _get(url: str, headers: dict[str, str] | None = None,
         timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"user-agent": USER_AGENT, "accept": "application/json",
                      **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        # 4xx that is not rate limiting will not fix itself on retry.
        permanent = exc.code in (400, 401, 403, 404)
        raise SourceUnavailable(f"{url} -> {exc.code}: {body}", permanent=permanent) from exc
    except urllib.error.URLError as exc:
        raise SourceUnavailable(f"{url} unreachable: {exc.reason}") from exc


class Source(Protocol):
    platform: str

    def map_party(self, target: str) -> Harvest: ...


# --------------------------------------------------------------------------- Spotify

@dataclass
class SpotifySource:
    """Client-credentials flow. Public catalogue data only — no user scope needed.

    This is the adapter the roster is actually waiting on: both artists already have an
    operator-asserted Spotify URL, and `mode = 'owned'`, which is the strongest claim in
    the system. Reading that surface is not inference, it is the artist's own page.
    """

    client_id: str
    client_secret: str
    platform: str = "spotify"
    _token: str = ""
    _expires_at: float = 0.0

    @staticmethod
    def artist_id(target: str) -> str:
        """Accept a bare id, a URL or a URI, because all three get pasted into forms."""
        target = target.strip()
        if target.startswith("spotify:artist:"):
            return target.rsplit(":", 1)[-1]
        if "open.spotify.com" in target:
            path = urllib.parse.urlsplit(target).path.strip("/").split("/")
            if "artist" in path:
                return path[path.index("artist") + 1]
        return target

    def _bearer(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        request = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            headers={"authorization": f"Basic {basic}",
                     "content-type": "application/x-www-form-urlencoded",
                     "user-agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise SourceUnavailable(
                f"Spotify rejected the client credentials ({exc.code}). "
                f"Check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.", permanent=True,
            ) from exc
        self._token = payload["access_token"]
        # Refresh a minute early rather than on the boundary.
        self._expires_at = time.monotonic() + int(payload.get("expires_in", 3600)) - 60
        return self._token

    def _api(self, path: str) -> dict[str, Any]:
        return _get(f"https://api.spotify.com/v1{path}",
                    {"authorization": f"Bearer {self._bearer()}"})

    def map_party(self, target: str) -> Harvest:
        sid = self.artist_id(target)
        harvest = Harvest()

        artist = self._api(f"/artists/{sid}")
        harvest.calls += 1
        name = artist.get("name", "")
        harvest.summary = f"spotify: {name}"

        harvest.identifiers.append(
            {"kind": "spotify_artist", "value": sid, "provenance": "measured"})

        # Follower count and popularity are read straight off the platform: measured.
        followers = (artist.get("followers") or {}).get("total")
        if followers is not None:
            harvest.metrics.append({"metric": "followers", "value": float(followers),
                                    "unit": "count", "provenance": "measured"})
        if artist.get("popularity") is not None:
            harvest.metrics.append({"metric": "popularity",
                                    "value": float(artist["popularity"]),
                                    "unit": "score_0_100", "provenance": "measured"})

        # Genres are Spotify's classification of the artist, not a measurement of them.
        for genre in artist.get("genres") or []:
            harvest.facts.append({"dimension": "genre", "value_text": genre,
                                  "provenance": "inferred", "confidence": 0.6})

        # Top tracks carry ISRCs, which is the identifier the whole catalogue hangs off.
        top = self._api(f"/artists/{sid}/top-tracks?market=US")
        harvest.calls += 1
        for track in (top.get("tracks") or [])[:10]:
            harvest.recordings.append({
                "title": track.get("name", ""),
                "isrc": ((track.get("external_ids") or {}).get("isrc") or ""),
                "duration_ms": track.get("duration_ms"),
                "spotify_id": track.get("id"),
            })

        albums = self._api(f"/artists/{sid}/albums?include_groups=album,single&limit=50")
        harvest.calls += 1
        for album in albums.get("items") or []:
            harvest.releases.append({
                "title": album.get("name", ""),
                "release_date": album.get("release_date", ""),
                "kind": album.get("album_group") or album.get("album_type") or "",
                "spotify_id": album.get("id"),
                "track_count": album.get("total_tracks"),
            })

        # Every release is worth mapping in its own right — that is the next frontier
        # hop, and it is a row rather than a function call.
        for release in harvest.releases[:25]:
            harvest.leads.append({
                "kind": "map_release", "adapter": "spotify", "platform": "spotify",
                "target": release["spotify_id"],
                "target_hash": f"spotify:album:{release['spotify_id']}",
                "reason": f"release '{release['title'][:60]}' found on the artist page",
                "score": 0.6,
            })

        return harvest


# ---------------------------------------------------------------------------- Deezer

@dataclass
class DeezerSource:
    """No key, no auth, free. The source that can run the moment it is enabled.

    Deezer is reached by *searching a name*, which makes every match inferred. So this
    adapter never writes a presence row — it writes `suggestion` rows for a human to
    accept, and only an accepted suggestion becomes an asserted surface.
    """

    platform: str = "deezer"

    @staticmethod
    def artist_id(target: str) -> str:
        """The numeric id, if this target is one — otherwise "".

        A lead's target is whatever created it: a name when discovery queued a search, a
        URL once an accepted suggestion wrote a presence row. Both arrive here, and the
        difference decides whether this searches or reads. Getting it wrong is quiet
        rather than loud — searching for the literal string "https://…" returns nothing
        and looks exactly like an artist with no Deezer page.
        """
        target = target.strip()
        if target.isdigit():
            return target
        if "deezer.com" in target:
            tail = urllib.parse.urlsplit(target).path.strip("/").split("/")
            if "artist" in tail:
                candidate = tail[tail.index("artist") + 1]
                if candidate.isdigit():
                    return candidate
        return ""

    def map_party(self, target: str) -> Harvest:
        harvest = Harvest()
        known = self.artist_id(target)
        if known:
            return self._by_id(known, harvest)
        return self._search(target, harvest)

    def _search(self, name: str, harvest: Harvest) -> Harvest:
        query = urllib.parse.urlencode({"q": name})
        found = _get(f"https://api.deezer.com/search/artist?{query}")
        harvest.calls += 1
        harvest.summary = f"deezer: {len(found.get('data') or [])} candidates for {name!r}"

        for candidate in (found.get("data") or [])[:5]:
            exact = candidate.get("name", "").casefold() == name.casefold()
            harvest.suggestions.append({
                "kind": "presence",
                "platform": "deezer",
                "value": str(candidate.get("id")),
                "url": candidate.get("link", ""),
                "label": candidate.get("name", ""),
                # An exact string match is still a guess: two acts share a name often
                # enough that this must never auto-accept.
                "confidence": 0.7 if exact else 0.3,
                "evidence": {"fans": candidate.get("nb_fan"),
                             "albums": candidate.get("nb_album")},
            })
        return harvest

    def _by_id(self, deezer_id: str, harvest: Harvest) -> Harvest:
        """Read a known artist page: identity, audience, catalogue.

        The ISRCs are the point. They are the join key between what a platform says an
        artist released and what a distributor statement says actually earned — the one
        identifier that survives across both, and the reason this fetches each top track
        individually rather than settling for the summary the list endpoint returns.
        """
        artist = _get(f"https://api.deezer.com/artist/{deezer_id}")
        harvest.calls += 1
        name = artist.get("name", "")
        harvest.summary = f"deezer: {name}"

        harvest.identifiers.append(
            {"kind": "deezer_artist", "value": str(deezer_id), "provenance": "measured"})
        if artist.get("nb_fan") is not None:
            harvest.metrics.append({"metric": "fans", "value": float(artist["nb_fan"]),
                                    "unit": "count", "provenance": "measured"})

        top = _get(f"https://api.deezer.com/artist/{deezer_id}/top?limit=10")
        harvest.calls += 1
        for track in (top.get("data") or [])[:10]:
            track_id = track.get("id")
            isrc = ""
            if track_id:
                # The list payload omits ISRC; the track resource carries it. One call
                # per track, bounded at ten, on a free unmetered API.
                try:
                    detail = _get(f"https://api.deezer.com/track/{track_id}")
                    harvest.calls += 1
                    isrc = (detail.get("isrc") or "").strip().upper()
                except SourceUnavailable:
                    # A single track failing is not the artist failing. Keep the title
                    # without the identifier rather than losing the recording.
                    pass
            harvest.recordings.append({
                "title": track.get("title", ""),
                "isrc": isrc,
                "duration_ms": (track.get("duration") or 0) * 1000 or None,
            })

        albums = _get(f"https://api.deezer.com/artist/{deezer_id}/albums?limit=25")
        harvest.calls += 1

        # `/top` is chart data, and an artist nobody has streamed yet has none of it —
        # which is every artist this product exists to help. Their catalogue is on their
        # releases, so that is where the recordings come from. Taking the top-tracks
        # endpoint as the catalogue would have quietly reported an unsigned artist as
        # having no songs.
        seen_isrc = {r["isrc"] for r in harvest.recordings if r["isrc"]}
        track_calls = 0
        for album in (albums.get("data") or [])[:25]:
            album_id = album.get("id")
            gtin = ""
            tracks: list[dict[str, Any]] = []
            if album_id:
                try:
                    detail = _get(f"https://api.deezer.com/album/{album_id}")
                    harvest.calls += 1
                    gtin = (detail.get("upc") or "").strip()
                    tracks = ((detail.get("tracks") or {}).get("data") or [])
                except SourceUnavailable:
                    pass

            harvest.releases.append({
                "title": album.get("title", ""),
                "release_date": album.get("release_date", ""),
                "kind": album.get("record_type") or "single",
                "gtin": gtin,
            })

            for track in tracks:
                isrc = (track.get("isrc") or "").strip().upper()
                if not isrc and track.get("id") and track_calls < 40:
                    try:
                        tdetail = _get(f"https://api.deezer.com/track/{track['id']}")
                        harvest.calls += 1
                        track_calls += 1
                        isrc = (tdetail.get("isrc") or "").strip().upper()
                    except SourceUnavailable:
                        pass
                if isrc and isrc in seen_isrc:
                    continue
                if isrc:
                    seen_isrc.add(isrc)
                harvest.recordings.append({
                    "title": track.get("title", ""),
                    "isrc": isrc,
                    "duration_ms": (track.get("duration") or 0) * 1000 or None,
                })

        return harvest


# -------------------------------------------------------------------------- registry

def manifest(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    """The source registry, as the database has it."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM source_manifest")
        return {row["platform"]: row for row in cur.fetchall()}


def build(platform: str) -> Source:
    """Construct an adapter, or say exactly which credential is missing.

    Raised as permanent: a missing client secret is not a transient failure and retrying
    it four more times with backoff only delays somebody reading the message.
    """
    if platform == "spotify":
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        if not (client_id and secret):
            raise SourceUnavailable(
                "spotify needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET — "
                "create a free app at developer.spotify.com/dashboard",
                permanent=True)
        return SpotifySource(client_id=client_id, client_secret=secret)

    if platform == "deezer":
        return DeezerSource()

    raise SourceUnavailable(f"no adapter for {platform!r}", permanent=True)


def enabled_for(conn: psycopg.Connection, platform: str) -> Source:
    """Build an adapter only if the manifest says the source is on.

    The check is against the database rather than against a constant, so enabling Deezer
    is an `UPDATE`, not a release.
    """
    row = manifest(conn).get(platform)
    if row is None:
        raise SourceUnavailable(f"{platform!r} is not in source_manifest", permanent=True)
    if not row["enabled"]:
        raise SourceUnavailable(
            f"{platform} is disabled: {row['disabled_reason'] or 'no reason recorded'}",
            permanent=True)
    return build(platform)
