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

# Suggestions only. `artist.type` is a free STRING — see schema/002_artist_type.sql
# for why the set is open rather than enumerated.
SUGGESTED_TYPES = [
    "band", "solo", "dj", "singer", "songwriter", "composer",
    "producer", "rapper", "orchestra", "ensemble", "duo", "collective",
]


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
