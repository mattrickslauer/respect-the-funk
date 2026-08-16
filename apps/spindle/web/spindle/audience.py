"""The roster artist's own audience, under her own grant.

`038_audience_profile.sql` carries the argument; the one-paragraph version, because it is
the thing a reader of this file will otherwise assume was an oversight:

    `037_creator_scout.sql` found that no platform returns an arbitrary creator's
    follower demographics, at any price, and refused to scrape. That finding is intact.
    This module is not an exception to it — it has a different subject. Instagram returns
    audience data *to the account holder*, and the account holder here is the artist the
    tenant represents. `037` is about strangers and may not ask; this is about ourselves
    and does not have to.

Two things it deliberately does not do.

**It never enumerates a follower.** There is no such endpoint, on any tier — the Graph API
returns `follower_demographics` as aggregate breakdowns and nothing else, and if one
appeared tomorrow this module would still not call it. What arrives here is the *shape* of
an audience: how much of it is in Chicago, how much is 25-34. That shape is the whole
value, because it is what tells a shortlist which stations are worth approaching.

**It never guesses at an absence.** Below 100 followers Instagram returns no breakdown at
all, and a revoked token returns none either. Those are different situations with
different remedies and this module writes down which one it saw, because a shortlist that
silently skips its geographic rerank looks exactly like a shortlist that ran it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import psycopg

from spindle import fleet

#: Meta's versioned host. Pinned rather than floating: Graph API versions are sunset on a
#: published schedule and an unpinned call silently changes shape underneath a parser. The
#: cost of pinning is that this constant is a maintenance item with a deadline — which is
#: the honest trade, because the alternative moves the deadline somewhere nobody is
#: looking. Overridable so that a sunset is a config change and not a redeploy.
GRAPH_VERSION = os.environ.get("INSTAGRAM_GRAPH_VERSION", "v21.0").strip() or "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

#: The grant. Named for the service, not for this deployment — `PLATFORM_*` is reserved
#: for our own configuration and a third party's token is not that, the rule
#: `podcastindex.py` sets for `PODCASTINDEX_API_KEY`.
TOKEN_VAR = "INSTAGRAM_ACCESS_TOKEN"
USER_VAR = "INSTAGRAM_USER_ID"

#: Below this, `follower_demographics` returns nothing. Meta's number, not ours, and it is
#: checked against `followers_count` *before* the insights call rather than inferred from
#: the error afterwards — see `demographics` for why that ordering is the whole point.
DEMOGRAPHICS_MIN_FOLLOWERS = 100

#: The breakdowns this module asks for, and the `audience_segment.dimension` each becomes.
#: Instagram's spelling is already ours, so the mapping is an identity today; it exists so
#: that YouTube's `ageGroup` has somewhere to become `age` without a second vocabulary
#: leaking into the table every consumer reads.
BREAKDOWNS: tuple[str, ...] = ("city", "country", "age", "gender")

#: How many recent posts feed the composed profile. Enough for a genre to be legible
#: across more than one release, few enough that one viral post cannot become the whole
#: description of an artist.
MEDIA_LIMIT = 25


class NotConfigured(RuntimeError):
    """No grant is present. Distinct from a grant that was refused."""


class Refused(RuntimeError):
    """Instagram answered, and the answer was no.

    `permanent` carries whether retrying could ever work, the same split
    `podcastindex.Refused` makes: a 429 belongs back on the frontier with a backoff, an
    invalid token does not improve on the fourth attempt.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def credentials() -> tuple[str, str]:
    """The token and the account it is for, or a message saying how to get them.

    Both or neither. A token with no account id has nothing to ask about, and accepting
    one would produce a 400 whose cause is two layers from its symptom.
    """
    token = os.environ.get(TOKEN_VAR, "").strip()
    user = os.environ.get(USER_VAR, "").strip()
    if not (token and user):
        missing = ", ".join(name for name, value in
                            ((TOKEN_VAR, token), (USER_VAR, user)) if not value)
        raise NotConfigured(
            f"Instagram needs {TOKEN_VAR} and {USER_VAR}; {missing} "
            f"{'is' if missing.count(',') == 0 else 'are'} not set. The account must be a "
            "Professional (Business or Creator) one — the Basic Display API that served "
            "personal accounts was shut down on 2024-12-04 and there is no other path. "
            "Create a Meta app, add the artist as a tester, and grant "
            "`instagram_basic` + `instagram_manage_insights`; a development-mode app "
            "needs no App Review for an account you control.")
    return token, user


def _get(path: str, params: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    """One authenticated GET, or a `Refused` that says which kind of no this was.

    Every branch raises. Nothing here returns a partial result to a caller who cannot tell
    it from a whole one — `podcastindex._get`'s rule, and for the same reason: an adapter
    whose errors degrade into `{}` turns an outage into an empty audience.
    """
    token, _ = credentials()
    query = dict(params)
    query["access_token"] = token
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        # Meta puts the meaningful code in the body rather than in the status: an expired
        # or revoked user token is 190 under a 400, which is a permanent failure wearing a
        # transient status code. Reading the body is what tells the two apart.
        code = _error_code(detail)
        if code == 190 or exc.code == 401:
            raise Refused(
                f"Instagram rejected the grant in {TOKEN_VAR} (code {code or exc.code}). "
                "A user token is revoked when the artist removes the app, changes her "
                "password, or the 60-day window lapses — all three need a person to "
                f"re-authorise, not a retry. Response: {detail}",
                permanent=True) from exc
        if exc.code == 429 or code == 4:
            raise Refused(
                "Instagram is rate-limiting this client. The lead goes back to the "
                "frontier on its backoff; if it recurs, lengthen the stage's "
                f"cadence rather than retrying harder. Response: {detail}",
                permanent=False) from exc
        raise Refused(
            f"Instagram answered {exc.code} for {path}: {detail}",
            permanent=400 <= exc.code < 500) from exc
    except urllib.error.URLError as exc:
        raise Refused(f"Instagram unreachable for {path}: {exc.reason}",
                      permanent=False) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(
            f"Instagram returned {len(raw)} bytes for {path} that are not JSON. "
            f"First 200: {raw[:200]!r}", permanent=False) from exc

    if not isinstance(body, dict):
        raise Refused(f"Instagram returned a {type(body).__name__} for {path}, not an "
                      "object", permanent=False)
    return body


def _error_code(detail: str) -> int | None:
    """Meta's own error code out of a response body, or None if it is not shaped like one.

    Best-effort by construction — the caller has already decided to raise and this only
    sharpens the message — so a body that will not parse returns None rather than
    raising a second error on top of the first.
    """
    try:
        parsed = json.loads(detail)
    except (json.JSONDecodeError, TypeError):
        return None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, int) else None


def account(user_id: str) -> dict[str, Any]:
    """The account's own record: how many followers, and what it is called.

    `followers_count` is fetched before any insights call because it is what decides
    whether the insights call can succeed at all, and knowing that in advance is what lets
    this module record `below_threshold` as a measurement rather than as a guess at the
    meaning of an error string.
    """
    body = _get(f"/{user_id}", {"fields": "username,followers_count"})
    if "followers_count" not in body:
        raise Refused(
            f"Instagram returned no `followers_count` for {user_id}. That field is "
            "present on every Professional account and absent on personal ones, so the "
            "likeliest cause is that the account was converted back. Response: "
            f"{json.dumps(body)[:300]}", permanent=True)
    return body


def demographics(user_id: str, *, followers: int) -> dict[str, list[tuple[str, float]]]:
    """The audience breakdown, normalised to shares, or `{}` when there cannot be one.

    Returns a mapping of dimension to `(value, share)` pairs, largest first. Shares sum to
    1 within each dimension: Instagram returns absolute counts per bucket and a count is
    not comparable across two captures of a growing account, which is why
    `audience_segment.share` is a fraction and the normalisation happens here rather than
    at read time.

    The empty mapping means *the platform will not answer for a reason we already know* —
    the account is below `DEMOGRAPHICS_MIN_FOLLOWERS`. That is checked here against a
    number we hold rather than inferred from the text of an error, so the caller never has
    to pattern-match a vendor's prose to find out what happened.
    """
    if followers < DEMOGRAPHICS_MIN_FOLLOWERS:
        return {}

    out: dict[str, list[tuple[str, float]]] = {}
    for breakdown in BREAKDOWNS:
        body = _get(f"/{user_id}/insights", {
            "metric": "follower_demographics",
            "period": "lifetime",
            "metric_type": "total_value",
            "breakdown": breakdown,
        })
        pairs = _breakdown_pairs(body, breakdown)
        if pairs:
            out[breakdown] = pairs
    return out


def _breakdown_pairs(body: dict[str, Any], breakdown: str) -> list[tuple[str, float]]:
    """`(bucket, share)` out of one insights envelope, or `[]` if it carried no results.

    The envelope is four levels deep and every level is optional in the wild, so each is
    checked rather than indexed. A shape this module does not recognise raises: a silently
    empty breakdown would be written as "this artist has no followers in any city", which
    is a claim, and the wrong one.
    """
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise Refused(
            f"Instagram's {breakdown} breakdown carried no `data` array. "
            f"Response: {json.dumps(body)[:300]}", permanent=False)

    total_value = data[0].get("total_value") if isinstance(data[0], dict) else None
    if not isinstance(total_value, dict):
        return []
    breakdowns = total_value.get("breakdowns")
    if not isinstance(breakdowns, list) or not breakdowns:
        return []
    results = breakdowns[0].get("results") if isinstance(breakdowns[0], dict) else None
    if not isinstance(results, list):
        return []

    counts: list[tuple[str, float]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        values = result.get("dimension_values")
        count = result.get("value")
        if not isinstance(values, list) or not values:
            continue
        if not isinstance(count, (int, float)):
            continue
        counts.append((str(values[0]), float(count)))

    total = sum(count for _, count in counts)
    if total <= 0:
        return []
    pairs = [(value, count / total) for value, count in counts]
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    return pairs


def recent_media(user_id: str, *, limit: int = MEDIA_LIMIT) -> list[dict[str, Any]]:
    """The artist's own recent posts — captions and engagement, not the images.

    This is the half of the signal that does not need 100 followers, and for matching
    counterparties it is the more useful half: a caption carries genre, collaborators and
    the words the artist uses about her own work, and those are what a vector search over
    counterparties actually matches on.
    """
    body = _get(f"/{user_id}/media", {
        "fields": "caption,like_count,comments_count,timestamp,permalink",
        "limit": int(limit),
    })
    data = body.get("data")
    if not isinstance(data, list):
        raise Refused(
            f"Instagram returned no `data` array for {user_id}'s media. "
            f"Response: {json.dumps(body)[:300]}", permanent=False)
    return [item for item in data if isinstance(item, dict)]


def compose_profile(name: str, snapshot: dict[str, Any]) -> str:
    """The prose an embedding is taken of.

    `profile_creator` composes a creator's vibe out of `sound_usage` rows and hands it to
    `embed_party`; this does the same for an artist out of her own audience and captions,
    and deliberately produces the *same kind of artefact* — a `party_document` — so that
    the artist lands in the same vector space as every counterparty. A private embedding
    space for our own roster would make the one comparison this system exists to draw
    impossible to compute.

    Written as sentences rather than as a table because that is what the embedding model
    was trained on. An absent breakdown is stated as absent, in words, for the same reason
    the column exists.
    """
    lines: list[str] = [f"{name} — audience and recent work, from her own Instagram."]

    followers = snapshot.get("follower_count")
    if followers is not None:
        lines.append(f"She has {followers:,} followers.")

    state = snapshot.get("demographics_state", "present")
    segments: dict[str, list[tuple[str, float]]] = snapshot.get("segments") or {}

    if state != "present":
        lines.append(
            "Her audience breakdown is unavailable: "
            f"{snapshot.get('unavailable_detail', 'no reason recorded')}. "
            "Nothing below describes where her listeners are, because we do not know.")
    else:
        for dimension, label in (("country", "countries"), ("city", "cities")):
            pairs = segments.get(dimension) or []
            if pairs:
                top = ", ".join(f"{value} ({share:.0%})" for value, share in pairs[:5])
                lines.append(f"Her audience concentrates in these {label}: {top}.")
        age = segments.get("age") or []
        if age:
            top = ", ".join(f"{value} ({share:.0%})" for value, share in age[:3])
            lines.append(f"Most of them are aged {top}.")
        gender = segments.get("gender") or []
        if gender:
            top = ", ".join(f"{value} ({share:.0%})" for value, share in gender)
            lines.append(f"By gender the split is {top}.")

    captions = [str(item.get("caption", "")).strip()
                for item in snapshot.get("media") or []]
    captions = [caption for caption in captions if caption]
    if captions:
        lines.append("In her own words, across recent posts:")
        lines.extend(f"— {caption}" for caption in captions)

    return "\n".join(lines)


# ---------------------------------------------------------------- refresh_audience ---

def _fetch_refresh_audience(conn: psycopg.Connection, lead: dict[str, Any],
                            gate: Any) -> dict[str, Any]:
    """Read who this lead is about, then ask Instagram about her. Writes nothing.

    The subject check happens here rather than in `write` because it decides whether to
    make the call at all, and because `038`'s header owes the reader an enforcement point
    it can name: a `CHECK` may not reference `party`, so this is where "the subject is one
    of ours" is actually decided. A counterparty reaching this agent is a programming
    error rather than a bad row, so it is permanent — retrying cannot make her ours.
    """
    party_id = str(lead.get("party_id") or "").strip()
    if not party_id:
        raise fleet.LeadFailed("refresh_audience needs a party_id", permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, party_class FROM party
                WHERE tenant_id = %s AND id = %s""",
            (lead["tenant_id"], party_id))
        party = cur.fetchone()

    if party is None:
        raise fleet.LeadFailed(f"no party {party_id} in this tenant", permanent=True)
    if party["party_class"] != "roster":
        raise fleet.LeadFailed(
            f"{party['name']} is a {party['party_class']}, and this agent reads audience "
            "data under the subject's own OAuth grant. `037_creator_scout.sql` is the "
            "standing finding that we may not hold that data for anybody who has not "
            "granted it, and a counterparty has not.", permanent=True)

    try:
        token, user_id = credentials()
    except NotConfigured as exc:
        # Not permanent. A grant that has not been wired up yet is an errand, and the
        # lead should be waiting when somebody runs it — not parked where it needs a
        # hand-crank to come back.
        raise fleet.LeadFailed(str(exc), permanent=False) from exc

    try:
        profile = account(user_id)
        followers = int(profile.get("followers_count") or 0)
        segments = demographics(user_id, followers=followers)
        media = recent_media(user_id)
    except Refused as exc:
        if exc.permanent:
            # A revoked grant is a fact about the world worth recording, not just a failed
            # run: the console has to be able to say "she withdrew access on Tuesday"
            # rather than showing a stale profile with no explanation beside it.
            return {"party": dict(party), "revoked": str(exc)}
        raise fleet.LeadFailed(str(exc), permanent=False) from exc

    state = "present" if segments else "below_threshold"
    detail = "" if segments else (
        f"the account has {followers} followers and Instagram returns no "
        f"`follower_demographics` below {DEMOGRAPHICS_MIN_FOLLOWERS}")

    return {
        "party": dict(party),
        "account_ref": str(profile.get("username") or user_id),
        "follower_count": followers,
        "demographics_state": state,
        "unavailable_detail": detail,
        "segments": segments,
        "media": media,
    }


def _write_refresh_audience(conn: psycopg.Connection, lead: dict[str, Any],
                            gate: Any, prepared: dict[str, Any]) -> fleet.Outcome:
    """Append one immutable snapshot, and queue the composition that reads it back.

    Append rather than update: `038`'s header sets out why history here is rows and not
    `AS OF SYSTEM TIME`, and the short version is that this cluster's garbage collection
    window is 75 minutes, so an overwritten profile is gone before anybody asks a question
    worth asking about it.
    """
    tenant_id = lead["tenant_id"]
    party = prepared["party"]
    party_id = party["id"]

    if "revoked" in prepared:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audience_profile
                       (tenant_id, party_id, platform, demographics_state,
                        unavailable_detail, lead_id)
                   VALUES (%s, %s, 'instagram', 'not_granted', %s, %s)""",
                (tenant_id, party_id, prepared["revoked"][:500], lead["id"]))
        return fleet.Outcome(
            summary=f"{party['name']}: Instagram grant refused, recorded as not_granted",
            documents=0)

    segments: dict[str, list[tuple[str, float]]] = prepared["segments"]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audience_profile
                   (tenant_id, party_id, platform, account_ref, follower_count,
                    demographics_state, unavailable_detail, lead_id)
               VALUES (%s, %s, 'instagram', %s, %s, %s, %s, %s)
            RETURNING id""",
            (tenant_id, party_id, prepared["account_ref"], prepared["follower_count"],
             prepared["demographics_state"], prepared["unavailable_detail"], lead["id"]))
        profile_id = cur.fetchone()["id"]

        rows = 0
        for dimension, pairs in segments.items():
            for value, share in pairs:
                cur.execute(
                    """INSERT INTO audience_segment
                           (tenant_id, profile_id, dimension, value, share)
                       VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, profile_id, dimension, value)
                    DO NOTHING""",
                    (tenant_id, profile_id, dimension, value[:200], share))
                rows += 1

    return fleet.Outcome(
        summary=(f"{party['name']}: {prepared['follower_count']:,} followers, "
                 f"{rows} audience segment(s), {len(prepared['media'])} recent post(s)"),
        facts=rows,
        follow_on=[{
            "kind": "profile_audience", "adapter": "internal", "party_id": str(party_id),
            "scope_kind": "party", "target": str(party_id),
            "target_hash": f"profile_audience:{profile_id}",
            "reason": "audience snapshot captured, not yet composed", "score": 0.8,
        }],
    )


#: Ask Instagram what her audience looks like, and write down what it said — including
#: when what it said was no.
refresh_audience = fleet.NetworkAgent(fetch=_fetch_refresh_audience,
                                      write=_write_refresh_audience)


# ---------------------------------------------------------------- profile_audience ---

def profile_audience(conn: psycopg.Connection, lead: dict[str, Any],
                     gate: Any) -> fleet.Outcome:
    """Compose the latest snapshot into prose and queue `embed_party`.

    Reads the newest `audience_profile` rather than taking one as an argument, so that a
    re-run after a composition bug uses the freshest capture and never spends another call
    on a platform whose rate limits we do not control. That is the whole reason this is a
    second agent instead of the tail of the first.

    It makes no network call itself. The embedding is `embed_party`'s to pay for, in the
    same vector space as every counterparty — see `compose_profile`.
    """
    from spindle.agents import content_hash

    tenant_id = lead["tenant_id"]
    party_id = str(lead.get("party_id") or "").strip()
    if not party_id:
        raise fleet.LeadFailed("profile_audience needs a party_id", permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT a.id, a.follower_count, a.demographics_state,
                      a.unavailable_detail, p.name
                 FROM audience_profile a
                 JOIN party p ON p.tenant_id = a.tenant_id AND p.id = a.party_id
                WHERE a.tenant_id = %s AND a.party_id = %s AND a.platform = 'instagram'
                ORDER BY a.captured_at DESC
                LIMIT 1""",
            (tenant_id, party_id))
        profile = cur.fetchone()

        if profile is None:
            raise fleet.LeadFailed(
                "no Instagram audience profile for this party yet, so there is nothing "
                "to compose — run `refresh_audience` first", permanent=False)

        cur.execute(
            """SELECT dimension, value, share FROM audience_segment
                WHERE tenant_id = %s AND profile_id = %s
                ORDER BY dimension, share DESC""",
            (tenant_id, profile["id"]))
        segment_rows = cur.fetchall()

    segments: dict[str, list[tuple[str, float]]] = {}
    for row in segment_rows:
        segments.setdefault(row["dimension"], []).append(
            (row["value"], float(row["share"])))

    body = compose_profile(profile["name"], {
        "follower_count": profile["follower_count"],
        "demographics_state": profile["demographics_state"],
        "unavailable_detail": profile["unavailable_detail"],
        "segments": segments,
        # Captions are not re-read here: they were fetched under the grant and belong to
        # the snapshot's moment, not to this one. The composed document is the artefact
        # that carries them forward.
        "media": [],
    })

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, lead_id, platform,
                                               url, title, body, content_hash, mime,
                                               lang, http_status)
                   VALUES (%s, %s, %s, 'instagram', '', %s, %s, %s, 'text/plain', 'en', 200)
                   ON CONFLICT (tenant_id, content_hash) DO NOTHING""",
                (tenant_id, party_id, lead["id"],
                 f"{profile['name']} — composed audience profile", body,
                 content_hash(body)))

    return fleet.Outcome(
        summary=(f"composed {profile['name']}'s audience profile from "
                 f"{len(segment_rows)} segment(s)"),
        documents=1,
        follow_on=[{
            "kind": "embed_party", "adapter": "internal", "party_id": str(party_id),
            "scope_kind": "party", "target": str(party_id),
            "target_hash": f"embed_party:{party_id}",
            "reason": "audience profile composed, not yet searchable", "score": 0.7,
        }],
    )


# ---------------------------------------------------------------- the geographic rerank ---

#: How far a counterparty is moved by sitting in a country the artist's whole audience is
#: in. A share of 1.0 shifts the distance by this much and no more.
#:
#: 0.08 against distances that typically run 0.1–0.6, chosen beside `lessons.LESSON_WEIGHT`
#: (0.05) rather than independently of it, because the two rerank the same list and their
#: relative sizes are the only thing that decides which one wins an argument. A 62% country
#: shifts by ~0.05 — about one fully-confident lesson — which is the intended reading:
#: *where her audience already is* is evidence of roughly the same strength as *what
#: happened last time we pitched this curator*, and neither should be able to lift a poor
#: match over a good one on its own.
#:
#: A starting value chosen before there was a campaign to tune it against, and a named
#: constant so that changing it is a visible change rather than a silent one.
AUDIENCE_WEIGHT = 0.08


def geo_rerank(candidates: list[dict[str, Any]],
               segments: dict[str, list[tuple[str, float]]], *,
               weight: float = AUDIENCE_WEIGHT) -> list[dict[str, Any]]:
    """Lift counterparties who are where the artist's audience already is.

    Deliberately the same shape as `lessons.rerank`: pure, returns new rows, sorted
    ascending because these are distances, and every shift is returned beside the thing
    that caused it. The two compose — this reads `adjusted` when the lesson rerank has
    already run and falls back to the raw `distance` when it has not — so the order they
    are applied in changes nothing about the result.

    `applied` is appended to rather than replaced, because a candidate that moved for two
    different reasons has to be able to say both. The console renders that list as the
    answer to "why is this station third", and a reason that overwrote another reason
    would make that answer a lie of omission.

    **An empty `segments` returns the candidates unmoved, and that is the whole
    contract.** Below 100 followers, or under a revoked grant, there is no geography to
    rank on, and inventing a neutral one would produce a ranking that looks
    audience-aware and is not — `038`'s argument for `demographics_state` existing at all.
    A caller that wants to say "this ranking used no audience data" reads the absence of
    `audience` entries in `applied`.
    """
    countries = dict(segments.get("country") or [])
    if not countries:
        return sorted(candidates,
                      key=lambda row: row.get("adjusted", row["distance"]))

    out: list[dict[str, Any]] = []
    for candidate in candidates:
        base = float(candidate.get("adjusted", candidate["distance"]))
        country = str(candidate.get("country") or "").strip().upper()
        share = countries.get(country, 0.0)
        applied = list(candidate.get("applied") or [])

        if share > 0:
            shift = share * weight
            base = max(0.0, base - shift)
            applied.append({
                "kind": "audience",
                "country": country,
                "share": share,
                # Signed like a lesson's, and always positive here: being where the
                # audience is can lift a candidate and never sinks one. Absence of
                # evidence that her listeners are in a country is not evidence that they
                # are not — most countries have a share of zero simply because Instagram
                # returns only the top buckets.
                "shift": shift,
                "text": (f"{share:.0%} of the artist's Instagram audience is in "
                         f"{country}"),
            })

        out.append({**candidate, "adjusted": base, "applied": applied})

    out.sort(key=lambda row: row["adjusted"])
    return out
