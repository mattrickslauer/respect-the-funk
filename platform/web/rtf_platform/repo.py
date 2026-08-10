"""Reads and writes against the spine. Plain SQL, no ORM.

Every query is scoped by `tenant_id` in its WHERE clause, without exception. That
column exists precisely so this is mechanical, and the moment one query omits it,
the boundary it buys stops being real.

There is no seed file. The label row is created by `ensure_tenant` the first time
somebody saves an artist, so a fresh cluster becomes a working one through the UI
rather than through a script somebody has to remember to run.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import psycopg

from rtf_platform import harvested

def slugify(value: str) -> str:
    """A URL-safe key derived from the display name.

    Accents are folded rather than dropped, so `Beyoncé` becomes `beyonce` and not
    `beyonc`. Names that reduce to nothing at all (scripts with no ASCII form) get
    an empty slug, and the caller is expected to reject that rather than store it.
    """
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")


# --------------------------------------------------------------------- tenant

def get_tenant(conn: psycopg.Connection, slug: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, slug, name FROM tenant WHERE slug = %s", (slug,))
        return cur.fetchone()


def ensure_tenant(conn: psycopg.Connection, slug: str, name: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
            (slug, name),
        )
    tenant = get_tenant(conn, slug)
    assert tenant is not None  # the insert above guarantees it exists
    return tenant


# --------------------------------------------------------------------- parties

#: The roster is `party_role`, not a table. A label's artists, its writers and the
#: creators it talks to are all parties; only the role differs, and a party may hold
#: several — at an independent label the artist is usually also the writer.
ROSTER_ROLE = "roster_artist"

#: `artist_type` is selected as `type` throughout. The column is named for what it
#: means (a presentation kind that only performers have), and the alias is what the
#: console has always called it — keeping both means neither the schema nor the view
#: layer has to carry the other's vocabulary.
_PARTY_COLUMNS = "id, slug, name, kind, artist_type AS type, status, created_at"
_PARTY_COLUMNS_P = ("p.id, p.slug, p.name, p.kind, p.artist_type AS type, "
                    "p.status, p.created_at")


def list_parties(conn: psycopg.Connection, tenant_id: str, query: str = "",
                 role: str | None = ROSTER_ROLE) -> list[dict[str, Any]]:
    """Parties, optionally narrowed to one role.

    `role=None` returns every party the tenant knows, which is what the counterparty
    view will want. The default is the roster, because that is what "artists" means
    everywhere it is used today.
    """
    sql = f"SELECT {_PARTY_COLUMNS_P} FROM party p"
    params: list[Any] = []
    if role:
        sql += " JOIN party_role r ON r.party_id = p.id AND r.role = %s"
        params.append(role)
    sql += " WHERE p.tenant_id = %s"
    params.append(tenant_id)
    if query:
        sql += " AND (p.name ILIKE %s OR p.artist_type ILIKE %s)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY p.name"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_party(conn: psycopg.Connection, tenant_id: str,
              party_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PARTY_COLUMNS} FROM party WHERE tenant_id = %s AND id = %s",
            (tenant_id, party_id),
        )
        return cur.fetchone()


def create_party(
    conn: psycopg.Connection, tenant_id: str, *, name: str, type_: str,
    kind: str = "group", status: str = "active", role: str | None = ROSTER_ROLE,
) -> dict[str, Any]:
    """The party and its role in one transaction.

    A party with no role is invisible to every view that filters by one, so creating
    the two separately would let a crash between them strand a row nobody can find.
    The connection is autocommit, so the transaction has to be asked for.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO party (tenant_id, slug, name, kind, artist_type, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                 RETURNING {_PARTY_COLUMNS}""",
                (tenant_id, slugify(name), name, kind, type_, status),
            )
            party = cur.fetchone()
            if role:
                cur.execute(
                    """INSERT INTO party_role (tenant_id, party_id, role)
                       VALUES (%s, %s, %s)
                  ON CONFLICT (tenant_id, party_id, role) DO NOTHING""",
                    (tenant_id, party["id"], role),
                )
    return party


def update_party(
    conn: psycopg.Connection, tenant_id: str, party_id: str, *,
    name: str, type_: str, status: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE party SET slug = %s, name = %s, artist_type = %s, status = %s
                 WHERE tenant_id = %s AND id = %s
             RETURNING {_PARTY_COLUMNS}""",
            (slugify(name), name, type_, status, tenant_id, party_id),
        )
        return cur.fetchone()


def delete_party(conn: psycopg.Connection, tenant_id: str, party_id: str) -> bool:
    """Deletes the party and the rows the database cannot cascade for it.

    `presence` is polymorphic — `subject_id` carries no foreign key, which is the
    price of one table serving parties, recordings and releases — so `ON DELETE
    CASCADE` never fires for it. Left behind, those rows are not merely untidy: the
    probe reconciler works from presence, so a deleted artist would keep being
    fetched forever and nothing would explain why.

    `lesson` is the same polymorphism for the same reason — `011_lesson.sql` scopes a
    lesson by `scope_kind`/`scope_id`, a `STRING` with no foreign key, because a lesson
    can be about a curator, a kind of counterparty, a channel, or everything, and only
    the first of those is a UUID. `scope_id` is compared as text here (`party_id` is a
    UUID; the column that holds it is not) for the same reason.

    Everything with a real foreign key (`party_role`, `party_identifier`,
    `party_credit`, `party_fact`, …) still cascades in the database, where it
    belongs. This function exists for the exceptions, not instead of them.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM presence
                    WHERE tenant_id = %s AND subject_kind = 'party'
                      AND subject_id = %s""",
                (tenant_id, party_id),
            )
            cur.execute(
                """DELETE FROM lesson
                    WHERE tenant_id = %s AND scope_kind = 'party'
                      AND scope_id = %s""",
                (tenant_id, str(party_id)),
            )
            cur.execute("DELETE FROM party WHERE tenant_id = %s AND id = %s",
                        (tenant_id, party_id))
            return cur.rowcount > 0


def list_roles(conn: psycopg.Connection, tenant_id: str, party_id: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role FROM party_role WHERE tenant_id = %s AND party_id = %s"
            " ORDER BY role",
            (tenant_id, party_id),
        )
        return [r["role"] for r in cur.fetchall()]


# -------------------------------------------------------------------- presence

#: `presence` is polymorphic over party, recording and release, so every call here
#: names its subject kind rather than assuming one. The console only edits party
#: presence today; the recording and release grids read the same table.
def list_presence(conn: psycopg.Connection, tenant_id: str, subject_id: str,
                  subject_kind: str = "party") -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, platform, mode, handle, url AS profile_url, state,
                      match_basis, enabled
                 FROM presence
                WHERE tenant_id = %s AND subject_kind = %s AND subject_id = %s
                ORDER BY platform""",
            (tenant_id, subject_kind, subject_id),
        )
        return cur.fetchall()


def upsert_presence(
    conn: psycopg.Connection, tenant_id: str, subject_id: str, *,
    platform: str, mode: str, handle: str = "", profile_url: str = "",
    subject_kind: str = "party", match_basis: str = "asserted",
) -> dict[str, Any]:
    """One row per (subject, platform) — the table says so, and the editor relies on it.

    An UPSERT rather than an insert-then-catch: adding Spotify twice is the operator
    correcting the handle they just typed, not an error worth showing them. The
    conflict target is the table's own unique key, so this cannot drift from it.

    `match_basis` defaults to `asserted` because the caller is a person filling in a
    form. An adapter that finds the same surface writes `measured`, and the two must
    not be confused: one of them is somebody's judgement.

    **Adding a surface also queues the work of mapping it.** The presence row and its
    `map_source` lead are written in one transaction, so a source can never sit in the
    console looking connected while nothing has ever gone and read it. That is the whole
    behaviour the operator is asking for when they paste a URL — they are not recording
    a bookmark, they are saying "go and find out what is there."

    The lead carries the source's own `cadence_seconds` from `source_manifest`, which
    makes it a recurring frontier entry rather than a one-shot: follower counts move, and
    a catalogue that was mapped once in August is wrong by October. A source with no
    cadence stays one-shot, which is `lead`'s single nullable column doing the work.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO presence
                        (tenant_id, subject_kind, subject_id, platform, mode, handle,
                         url, state, match_basis)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'present', %s)
              ON CONFLICT (tenant_id, subject_kind, subject_id, platform) DO UPDATE
                      SET mode = excluded.mode,
                          handle = excluded.handle,
                          url = excluded.url,
                          match_basis = excluded.match_basis,
                          checked_at = now()
                RETURNING id, platform, mode, handle, url AS profile_url, state, enabled""",
                (tenant_id, subject_kind, subject_id, platform, mode, handle,
                 profile_url, match_basis),
            )
            row = cur.fetchone()

            if subject_kind == "party":
                cur.execute(
                    "SELECT cadence_seconds FROM source_manifest WHERE platform = %s",
                    (platform,),
                )
                manifest = cur.fetchone()
                target = profile_url or handle
                if target:
                    cur.execute(
                        """INSERT INTO lead
                                (tenant_id, scope_kind, party_id, kind, mode, adapter,
                                 target, target_hash, platform, reason, score,
                                 cadence_seconds)
                           VALUES (%s, 'party', %s, 'map_source', 'auto', %s, %s, %s, %s,
                                   'a source was added and has not been mapped', 0.9, %s)
                           ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
                        (tenant_id, subject_id, platform, target,
                         f"map:{platform}:{subject_id}", platform,
                         manifest["cadence_seconds"] if manifest else None),
                    )
    return row


# ------------------------------------------------------------------ suggestions

class SuggestionUnacceptable(ValueError):
    """A suggestion cannot be promoted as it stands. Raised rather than defaulting
    the missing field (that is the exact bug this replaced) and rather than letting
    `harvested.HarvestInvalid` — an internal parsing detail — reach the console as
    an unhandled 500. Carries `suggestion_id` and an operator-readable `reason`, so
    the route that catches this has a message worth showing rather than a stack
    trace.

    Two things raise it: a suggestion `kind` this function has no accept path for
    (only `presence` is handled today — a future `merge`-kind suggestion from the
    lessons plan must not be blindly parsed as a `Presence` and fail on fields it
    was never going to carry), and a `presence`-kind suggestion whose payload
    predates a field `Presence.parse` now requires — measured on the live cluster
    at the time this was written: 0 of 8 existing `suggestion` rows carry a `mode`
    key, because they were written before `mode` became required. None were
    `pending`, so nothing was actively broken, but the next one written by an
    out-of-date adapter, or read back after a schema change, would have been.
    """

    def __init__(self, suggestion_id: str, reason: str) -> None:
        self.suggestion_id = suggestion_id
        self.reason = reason
        super().__init__(f"suggestion {suggestion_id}: {reason}")


def accept_suggestion(conn: psycopg.Connection, tenant_id: str,
                      suggestion_id: str, *, by: str = "operator") -> bool:
    """Promote an inferred match to an asserted surface, and queue the mapping.

    This is the only place in the system where a guess becomes a fact, so it is the one
    place the provenance changes class: the agent wrote `inferred` because it matched a
    name, and a person clicking Accept is the thing that makes it `asserted`. They are
    now the accountable party, which is precisely what `SCOPE-RESET §2a`'s third
    provenance class means.

    `mode` — whether accepting this means the artist owns the account — is read off
    the suggestion via `harvested.Presence.parse`, not defaulted here. `mode='owned'`
    is the strongest ownership claim `presence` carries; inventing it for a
    suggestion that never said so is the same defect class as inventing `measured`
    for a fact an adapter never labelled. The adapter that wrote the suggestion is
    the only thing that knows what accepting it means, so it says so in the payload.

    Only `kind == 'presence'` is handled — every suggestion this codebase writes
    today. Any other kind, and a `presence`-kind payload that fails to parse (an
    old row written before a field became required, most likely `mode`), raises
    `SuggestionUnacceptable` rather than either defaulting the missing field or
    letting the parser's `HarvestInvalid` reach the caller as an unhandled
    exception — see that class's docstring.

    One transaction, and `upsert_presence` runs inside it — so accepting also writes the
    `map_source` lead, and there is no window where a surface exists that nothing has
    been told to go and read.

    Sibling candidates for the same party and platform are rejected in the same breath.
    An artist has one page on a service; leaving four other maybes pending after one has
    been confirmed just asks the operator the same question again tomorrow.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, party_id, payload FROM suggestion
                    WHERE tenant_id = %s AND id = %s AND state = 'pending'
                      FOR UPDATE""",
                (tenant_id, suggestion_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            payload = row["payload"] or {}
            kind = payload.get("kind", "")
            if kind != "presence":
                raise SuggestionUnacceptable(
                    suggestion_id,
                    f"a {kind or 'unlabelled'} suggestion has no accept path yet")

            platform = payload.get("platform", "")
            if not platform:
                return False

            try:
                presence = harvested.Presence.parse(payload, adapter=platform)
            except harvested.HarvestInvalid as exc:
                raise SuggestionUnacceptable(
                    suggestion_id,
                    f"this suggestion is missing {exc.field!r} and cannot be "
                    "accepted automatically — it was written before that field "
                    "became required") from exc

            upsert_presence(
                conn, tenant_id, str(row["party_id"]),
                platform=presence.platform,
                mode=presence.mode,
                handle=presence.handle,
                profile_url=presence.profile_url,
                match_basis="asserted",
            )

            cur.execute(
                """UPDATE suggestion SET state = 'accepted', decided_by = %s,
                                         decided_at = now()
                    WHERE tenant_id = %s AND id = %s""",
                (by, tenant_id, suggestion_id),
            )
            cur.execute(
                """UPDATE suggestion SET state = 'superseded', decided_by = %s,
                                         decided_at = now()
                    WHERE tenant_id = %s AND party_id = %s AND state = 'pending'
                      AND payload->>'platform' = %s AND id != %s""",
                (by, tenant_id, row["party_id"], platform, suggestion_id),
            )
    return True


def reject_suggestion(conn: psycopg.Connection, tenant_id: str,
                      suggestion_id: str, *, by: str = "operator") -> bool:
    """Answer a candidate with no.

    Kept as a state change rather than a delete: "we looked at this and it is not them"
    is a finding, and a deleted row would let the same search re-suggest it next week
    with nothing to say it had already been dismissed.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE suggestion SET state = 'rejected', decided_by = %s,
                                     decided_at = now()
                WHERE tenant_id = %s AND id = %s AND state = 'pending'""",
            (by, tenant_id, suggestion_id),
        )
        return cur.rowcount > 0


def delete_presence(conn: psycopg.Connection, tenant_id: str, subject_id: str,
                    presence_id: str, subject_kind: str = "party") -> bool:
    """Scoped by subject as well as tenant. The id alone would be enough to find the
    row, which is exactly why it is not enough to authorise deleting it."""
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM presence
                WHERE tenant_id = %s AND subject_kind = %s AND subject_id = %s
                  AND id = %s""",
            (tenant_id, subject_kind, subject_id, presence_id),
        )
        return cur.rowcount > 0


# --------------------------------------------------------------- demo requests

#: Field caps. This is the only write in the system reachable without a session, so the
#: length limit is enforced here rather than trusted from the form — a `maxlength`
#: attribute is a hint to a browser, not a constraint on a POST.
_CAPS: dict[str, int] = {
    "name": 120, "label": 160, "email": 254, "roster_size": 40,
    "note": 2000, "user_agent": 400,
}


def create_demo_request(conn: psycopg.Connection, **fields: str) -> dict[str, Any]:
    """Record a demo request from the landing page.

    No tenant scoping, uniquely in this module: the whole point of the row is that the
    label filling it in does not have a tenant yet. Duplicates are allowed — somebody
    submitting twice is a person who wants to talk to you, not an integrity problem.
    """
    clean = {k: (fields.get(k) or "").strip()[:cap] for k, cap in _CAPS.items()}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO demo_request (name, label, email, roster_size, note,
                                         source, user_agent)
               VALUES (%(name)s, %(label)s, %(email)s, %(roster_size)s, %(note)s,
                       %(source)s, %(user_agent)s)
            RETURNING id, name, label, email, created_at""",
            {**clean, "source": (fields.get("source") or "landing")[:40]},
        )
        return cur.fetchone()
