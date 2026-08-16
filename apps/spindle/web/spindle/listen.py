"""Listen links: a pitch that carries the record, on a URL we own.

Before this, a pitch offered the master and asked the curator to reply for it. That is
good radio-servicing etiquette and it is still what the copy says when there is nothing
to link. When there *is* a stored master, the pitch now carries a per-recipient URL on
this deployment's own domain, and `routes.py` turns a click on it into a freshly-signed
S3 URL for the audio.

## Why the URL in the email is not the presigned one

Two reasons, and both are only visible in production. Migration 039's header has them at
length; the short version:

  * A presigned URL signed with the Lambda role's **temporary** credentials stops working
    when the session token does, whatever `ExpiresIn` said. The same code on a laptop,
    with long-lived credentials, works fine — so this fails exactly where it is not being
    watched. `storage.GET_TTL` is fifteen minutes regardless, which is dead before most
    cold email is opened.
  * A presigned URL cannot be withdrawn, and leaves no trace of having been used.

So the email carries `<base>/listen/<token>`, which is stable, revocable and countable,
and the S3 signature is minted at click time and lives for minutes.

## What this module refuses to do

`mint` raises rather than falling back when it has no public base URL to build on. A link
is the one artefact here that leaves the system and cannot be corrected afterwards: it is
sitting in somebody's inbox. A relative path, or a guess at the Function URL, is worse
than no link at all — which is why `append_link` composes a perfectly good pitch when
there is nothing to link, and why the two are separate functions.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import storage

#: How long a minted link stays good. Ninety days is chosen against the shape of the
#: activity rather than a round number: a programmer sits on a submission for weeks, and
#: a link that expires inside a month turns "I finally got to your record" into a 404.
#:
#: It is NOT NULL in the schema and there is no unlimited option, because an immortal
#: public download URL for a commercial master is not a thing to create by omission.
LINK_TTL_DAYS = 90

#: The lifetime of the S3 signature handed out on a click. Short on purpose and unrelated
#: to the link's own lifetime: the person is holding the page right now, and a signature
#: that outlives the visit is one that can be copied out of a network tab and shared.
AUDIO_TTL = storage.GET_TTL

#: `recording_asset.mime` → what to call it in a sentence a human reads. Explicit, with
#: no guess for anything absent: an unrecognised type yields "the master" rather than a
#: format claim, on exactly the argument `storage._SUFFIX` makes about inventing `.bin`.
#:
#: This is what stops the pitch promising a WAV and linking an MP3. The old copy said
#: "I can send a WAV" unconditionally while the only stored asset on this cluster is
#: `audio/mpeg` — a claim the schema could already have contradicted.
_FORMAT_NAME = {
    "audio/wav": "WAV",
    "audio/x-wav": "WAV",
    "audio/wave": "WAV",
    "audio/aiff": "AIFF",
    "audio/x-aiff": "AIFF",
    "audio/flac": "FLAC",
    "audio/mpeg": "MP3",
    "audio/mp4": "M4A",
    "audio/aac": "AAC",
    "audio/ogg": "OGG",
}


class LinkUnconfigured(RuntimeError):
    """No public base URL, so there is no address a link could point at."""


class TokenInvalid(RuntimeError):
    """The token does not resolve: unknown, revoked, or past `expires_at`.

    **One exception for all three states, deliberately.** The route renders the same 404
    whichever it is, so a caller holding a guessed token learns nothing about whether it
    was ever real — and a caller holding a revoked one is not told that revocation is
    what happened, which would confirm the token was valid once.
    """


@dataclass(frozen=True)
class Link:
    """What `mint` produces: the secret, the URL to put in a body, and when it dies."""

    token: str
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class Resolved:
    """Everything the landing page needs, from one query."""

    token_id: str
    tenant_id: str
    artist_name: str
    recording_title: str
    mime: str
    bucket: str
    object_key: str
    duration_ms: int | None

    @property
    def format_name(self) -> str:
        return format_name(self.mime)

    @property
    def duration_label(self) -> str:
        """`2:54`, or empty. Empty rather than `0:00` when the length is unknown —
        a duration nobody measured is not a duration of zero, and the template omits
        the whole clause rather than printing a number it made up."""
        if not self.duration_ms:
            return ""
        total = round(self.duration_ms / 1000)
        return f"{total // 60}:{total % 60:02d}"

    @property
    def download_filename(self) -> str:
        """`Hallow Youth - Losing Sleep.mp3`, or as close as the mime allows."""
        return (f"{self.artist_name} - {self.recording_title}"
                f"{storage.suffix_for(self.mime)}")


def format_name(mime: str) -> str:
    """What to call this file in a sentence. Never a guess — see `_FORMAT_NAME`."""
    return _FORMAT_NAME.get(mime, "audio")


def mint_token() -> str:
    """32 bytes of URL-safe randomness.

    Not a short code. `otp.mint_code` is six digits because a human retypes it from a
    mail client into a form, and it is defended by a five-minute expiry and an attempt
    counter. Nobody types this one — it is clicked — so it can afford to be wide enough
    that guessing is not a threat model at all, which is what lets it live for ninety
    days without an attempt counter behind it.
    """
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Lowercase hex sha256, which is what the database stores and checks.

    No salt and no KDF, on the same argument `otp.hash_code` makes: this is a 32-byte
    random value, not a human-chosen secret. There is no dictionary to attack, and
    stretching it would only slow the lookup that happens on every click.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint(conn: psycopg.Connection, tenant_id: str, *, message_id: str,
         recording_asset_id: str, counterparty_id: str, base_url: str,
         now: datetime | None = None) -> Link:
    """Create one link, for one recipient, for one message.

    Per-message rather than per-asset, so two pitches about the same record to two
    curators are distinguishable. Attribution that cannot tell them apart is not
    attribution.
    """
    if not base_url:
        raise LinkUnconfigured(
            "PLATFORM_PUBLIC_BASE_URL is not set, so there is no address a listen link "
            "could point at. Set it to this deployment's public origin (terraform "
            "output console_url) — it is deliberately not defaulted to the Function "
            "URL, because a pitch is the wrong place to discover that this deployment "
            "does not know its own name.")

    token = mint_token()
    at = now or datetime.now(timezone.utc)
    expires_at = at + timedelta(days=LINK_TTL_DAYS)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO listen_token (tenant_id, token_hash, message_id,
                                         recording_asset_id, counterparty_id,
                                         state, expires_at)
               VALUES (%s, %s, %s, %s, %s, 'active', %s)
            RETURNING id, expires_at""",
            (tenant_id, hash_token(token), message_id, recording_asset_id,
             counterparty_id, expires_at))
        row = cur.fetchone()

    assert row is not None  # INSERT ... RETURNING yields a row or raises
    return Link(token=token,
                url=f"{base_url.rstrip('/')}/listen/{token}",
                expires_at=row["expires_at"])


def resolve(conn: psycopg.Connection, raw_token: str,
            now: datetime | None = None) -> Resolved:
    """Turn a token from a URL into everything the page needs, or raise.

    The token is the credential and it carries its own tenant, which is why this does not
    take a `tenant_id` argument and must never grow one from the request. It is a
    separate function from anything in `auth.py` for the same reason
    `routes._showcase_tenant_id` is separate from `routes._tenant_id`: a resolver that
    can also answer "who is signed in" is one edit away from being an authentication
    fallback.

    The state check is in SQL rather than in Python so that an expired token cannot be
    read, inspected and then acted on by a caller that forgot to look.
    """
    at = now or datetime.now(timezone.utc)

    # **Two queries, and the split is the security boundary rather than a style choice.**
    #
    # This first one is the only statement in the module with no `tenant_id` predicate,
    # and it cannot have one: it is the query that *discovers* the tenant, so there is
    # nothing to scope by until it has answered. `account_by_token` in migration 033 is
    # the same shape for the same reason — the token is what finds the boundary.
    #
    # `tests/test_tenant_scoping.py` polices exactly this and is right to; the statement
    # is in its ALLOWLIST with this argument attached. Keeping it as narrow as possible
    # is what earns that entry: three columns off one table, no joins, and every
    # subsequent read scoped by the `tenant_id` it returned. A single joined query would
    # have been shorter and would have put five tables inside the unscoped statement.
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id, tenant_id, recording_asset_id
                 FROM listen_token
                WHERE token_hash = %s AND state = 'active' AND expires_at > %s""",
            (hash_token(raw_token), at))
        token_row = cur.fetchone()

    if token_row is None:
        raise TokenInvalid("no active listen token for that value")

    tenant_id = str(token_row["tenant_id"])

    # Everything from here is scoped by the tenant the token named, never by anything
    # read off the request.
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT p.name       AS artist_name,
                      r.title      AS recording_title,
                      a.mime       AS mime,
                      a.bucket     AS bucket,
                      a.object_key AS object_key,
                      COALESCE(a.duration_ms, r.duration_ms) AS duration_ms
                 FROM recording_asset a
                 JOIN recording r
                   ON r.id = a.recording_id AND r.tenant_id = a.tenant_id
                 -- The artist is reached through `party_credit`, because a recording
                 -- carries no artist column: migration 005 made the credit the join, so
                 -- that a feature or a co-write is expressible rather than a second
                 -- column nobody fills in. `main_artist` is the role the roster uses.
                 LEFT JOIN party_credit pc
                   ON pc.subject_kind = 'recording' AND pc.subject_id = r.id
                  AND pc.tenant_id = a.tenant_id AND pc.role = 'main_artist'
                 LEFT JOIN party p
                   ON p.id = pc.party_id AND p.tenant_id = a.tenant_id
                WHERE a.tenant_id = %s
                  AND a.id = %s
                  -- A row whose bytes were never PUT must not resolve. `analyse_
                  -- recording` applies the same filter for the same reason: a `pending`
                  -- asset is a promise nobody has kept, and presigning a GET for it
                  -- yields a link that 404s at S3 with no explanation.
                  AND a.state = 'stored'""",
            (tenant_id, token_row["recording_asset_id"]))
        row = cur.fetchone()

    if row is None:
        raise TokenInvalid("listen token resolves to no stored asset")

    return Resolved(
        token_id=str(token_row["id"]),
        tenant_id=tenant_id,
        # A recording with no `main_artist` credit is a data gap, not a reason to 500 on
        # a curator. The page renders without a name rather than refusing to play.
        artist_name=row["artist_name"] or "",
        recording_title=row["recording_title"] or "",
        mime=row["mime"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        duration_ms=row["duration_ms"],
    )


def record_event(conn: psycopg.Connection, resolved: Resolved, kind: str, *,
                 ip: str = "", user_agent: str = "") -> None:
    """Append one row. Never raises for a bad address or a missing header.

    The IP is hashed, not stored. It answers one question — is this the same person
    reloading, or the same email forwarded to five people — and the address itself would
    make this table personal data about somebody who never signed up for anything.
    """
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest() if ip else ""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO listen_event (tenant_id, listen_token_id, kind,
                                         ip_hash, user_agent)
               VALUES (%s, %s, %s, %s, %s)""",
            (resolved.tenant_id, resolved.token_id, kind, ip_hash, user_agent[:512]))


def revoke_for_counterparty(conn: psycopg.Connection, tenant_id: str,
                            counterparty_id: str) -> int:
    """Kill every live link for one counterparty. Returns how many.

    This is what ties the new surface into the consent model that already exists. A
    `contact_route` reaching `opted_out` or `bounced` means we must stop reaching this
    party — and a live download link in an email we already sent is still a way of
    reaching them, one that keeps working long after the address stops.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE listen_token
                  SET state = 'revoked', revoked_at = now()
                WHERE tenant_id = %s AND counterparty_id = %s AND state = 'active'""",
            (tenant_id, counterparty_id))
        return cur.rowcount


def append_link(body: str, link: Link | None, *, format_label: str = "") -> str:
    """Put the link into a pitch body, or leave the body alone.

    `None` is an ordinary input and the common one today: one stored master exists across
    the whole cluster, so most pitches have nothing to link. In that case the body is
    returned unchanged and whatever it already said about sending a file on request
    remains true.

    The format is named from the asset's own mime type rather than asserted by the copy.
    That is the whole point of threading it through: the previous wording promised a WAV
    on every pitch, including for a record whose only stored asset is an MP3.
    """
    if link is None:
        return body

    what = format_label or "master"
    return (f"{body.rstrip()}\n\n"
            f"Listen here, and download the {what} if you want it:\n"
            f"{link.url}\n")


def for_message(conn: psycopg.Connection, tenant_id: str, *, message_id: str,
                recording_asset_id: str | None, counterparty_id: str,
                base_url: str) -> tuple[Link | None, str]:
    """The drafter's one call: mint a link if there is anything to link.

    Returns `(link, format_label)`, both empty when the recording has no stored master.
    Separated from `mint` so that "there is nothing to link" and "this deployment cannot
    build a link" stay different outcomes — the first is ordinary and silent, the second
    raises, and collapsing them would make a misconfigured deployment look like a catalogue
    with no audio in it.
    """
    if recording_asset_id is None:
        return None, ""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT mime FROM recording_asset
                WHERE tenant_id = %s AND id = %s AND state = 'stored'""",
            (tenant_id, recording_asset_id))
        asset = cur.fetchone()

    if asset is None:
        return None, ""

    link = mint(conn, tenant_id, message_id=message_id,
                recording_asset_id=recording_asset_id,
                counterparty_id=counterparty_id, base_url=base_url)
    return link, format_name(asset["mime"])


def stored_master_for_recording(conn: psycopg.Connection, tenant_id: str,
                                recording_id: str) -> dict[str, Any] | None:
    """The master to pitch, or None. Newest wins when there is more than one."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT id, mime FROM recording_asset
                WHERE tenant_id = %s AND recording_id = %s
                  AND kind = 'master' AND state = 'stored'
             ORDER BY uploaded_at DESC NULLS LAST
                LIMIT 1""",
            (tenant_id, recording_id))
        return cur.fetchone()
