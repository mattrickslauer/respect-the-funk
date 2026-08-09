#!/usr/bin/env python3
"""Apply a migration file, with the guards a destructive one needs.

    python platform/schema/apply.py 005_party_first.sql
    python platform/schema/apply.py 005_party_first.sql --check   # guards only

`005` drops and recreates tables. That is safe only while they are empty, so the
emptiness is re-checked here against the live cluster rather than trusted from a
count taken earlier — the gap between deciding a migration is safe and running it
is exactly where a row lands.

Statements are split on `;` after stripping `--` comments. That is adequate for
these files and would not be for arbitrary SQL: it has no idea about semicolons
inside string literals. The migrations are written to keep it true, and this note
is here so nobody assumes otherwise later.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

#: Tables `005` drops. A row in any of them means the assumption behind the
#: migration has expired and somebody needs to look, so the default is to stop.
MUST_BE_EMPTY: tuple[str, ...] = (
    "artist_chunk", "fact_basis", "artist_fact", "artist_metric",
    "artist_budget", "agent_run", "artist_document", "lead", "artist_identifier",
)

#: Tables `005` carries over rather than drops. Reported, never blocked on.
CARRIED: tuple[str, ...] = ("tenant", "artist", "artist_profile")


def statements(sql: str) -> list[str]:
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def count(conn: psycopg.Connection, table: str) -> int | None:
    """Row count, or None if the table is not there — which is a normal answer
    when a migration has already run."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            return cur.fetchone()[0]
    except psycopg.errors.UndefinedTable:
        return None


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    check_only = "--check" in sys.argv
    if not args:
        print(__doc__)
        return 2

    path = Path(args[0])
    if not path.exists():
        path = Path(__file__).parent / args[0]
    if not path.exists():
        print(f"no such migration: {args[0]}")
        return 2

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2

    with psycopg.connect(url, autocommit=True) as conn:
        print("-- carried over --")
        for table in CARRIED:
            print(f"  {table:20s} {count(conn, table)}")

        print("-- must be empty --")
        blocked = []
        for table in MUST_BE_EMPTY:
            n = count(conn, table)
            state = "gone" if n is None else ("empty" if n == 0 else f"{n} ROWS")
            print(f"  {table:20s} {state}")
            if n:
                blocked.append((table, n))

        if blocked:
            print("\nREFUSING: these tables are not empty and the migration drops them:")
            for table, n in blocked:
                print(f"  {table} has {n} rows")
            return 1

        if check_only:
            print("\nguards pass — rerun without --check to apply")
            return 0

        stmts = statements(path.read_text())
        print(f"\napplying {path.name}: {len(stmts)} statements")
        for i, stmt in enumerate(stmts, 1):
            head = " ".join(stmt.split())[:78]
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
            except Exception as exc:  # noqa: BLE001 — the message is the product
                print(f"  {i:3d} FAILED  {head}")
                print(f"       {exc}")
                return 1
            print(f"  {i:3d} ok      {head}")

    print("\napplied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
