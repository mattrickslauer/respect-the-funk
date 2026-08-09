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


# --------------------------------------------------------------------- artists

def list_artists(conn: psycopg.Connection, tenant_id: str, query: str = "") -> list[dict[str, Any]]:
    sql = """
        SELECT id, slug, name, type, status, created_at
          FROM artist
         WHERE tenant_id = %s
    """
    params: list[Any] = [tenant_id]
    if query:
        sql += " AND (name ILIKE %s OR type ILIKE %s)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY name"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_artist(conn: psycopg.Connection, tenant_id: str, artist_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, slug, name, type, status, created_at
                 FROM artist WHERE tenant_id = %s AND id = %s""",
            (tenant_id, artist_id),
        )
        return cur.fetchone()


def create_artist(
    conn: psycopg.Connection, tenant_id: str, *, name: str, type_: str, status: str = "active"
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO artist (tenant_id, slug, name, type, status)
               VALUES (%s, %s, %s, %s, %s)
            RETURNING id, slug, name, type, status, created_at""",
            (tenant_id, slugify(name), name, type_, status),
        )
        return cur.fetchone()


def update_artist(
    conn: psycopg.Connection, tenant_id: str, artist_id: str, *, name: str, type_: str, status: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE artist SET slug = %s, name = %s, type = %s, status = %s
                WHERE tenant_id = %s AND id = %s
            RETURNING id, slug, name, type, status, created_at""",
            (slugify(name), name, type_, status, tenant_id, artist_id),
        )
        return cur.fetchone()


def delete_artist(conn: psycopg.Connection, tenant_id: str, artist_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM artist WHERE tenant_id = %s AND id = %s", (tenant_id, artist_id))
        return cur.rowcount > 0


# ------------------------------------------------------------- artist profiles

def list_profiles(conn: psycopg.Connection, tenant_id: str,
                  artist_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, platform, mode, handle, profile_url, enabled
                 FROM artist_profile
                WHERE tenant_id = %s AND artist_id = %s
                ORDER BY platform""",
            (tenant_id, artist_id),
        )
        return cur.fetchall()


def upsert_profile(
    conn: psycopg.Connection, tenant_id: str, artist_id: str, *,
    platform: str, mode: str, handle: str = "", profile_url: str = "",
) -> dict[str, Any]:
    """One row per (artist, platform) — the table says so, and the editor relies on it.

    An UPSERT rather than an insert-then-catch: adding Spotify twice is the operator
    correcting the handle they just typed, not an error worth showing them. The
    conflict target is the table's own unique key, so this cannot drift from it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO artist_profile
                    (tenant_id, artist_id, platform, mode, handle, profile_url)
               VALUES (%s, %s, %s, %s, %s, %s)
          ON CONFLICT (tenant_id, artist_id, platform) DO UPDATE
                  SET mode = excluded.mode,
                      handle = excluded.handle,
                      profile_url = excluded.profile_url
            RETURNING id, platform, mode, handle, profile_url, enabled""",
            (tenant_id, artist_id, platform, mode, handle, profile_url),
        )
        return cur.fetchone()


def delete_profile(conn: psycopg.Connection, tenant_id: str, artist_id: str,
                   profile_id: str) -> bool:
    """Scoped by artist as well as tenant. The id alone would be enough to find the
    row, which is exactly why it is not enough to authorise deleting it."""
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM artist_profile
                WHERE tenant_id = %s AND artist_id = %s AND id = %s""",
            (tenant_id, artist_id, profile_id),
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
