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

#: Tables a given migration drops. A row in any of them means the assumption behind
#: that migration has expired and somebody needs to look, so the default is to stop.
#:
#: Keyed by filename, because these guards are a property of one migration and not of
#: the runner. Getting this wrong in the safe direction is cheap and in the unsafe
#: direction is data loss, so a migration absent from this table is treated as
#: non-destructive and guarded by nothing — which is true of every migration that only
#: adds things, and is checked against the file below rather than assumed.
DESTRUCTIVE: dict[str, tuple[str, ...]] = {
    "005_party_first.sql": (
        "artist_chunk", "fact_basis", "artist_fact", "artist_metric",
        "artist_budget", "agent_run", "artist_document", "lead", "artist_identifier",
    ),
}

#: Tables a migration carries over rather than drops. Reported, never blocked on.
CARRIED: dict[str, tuple[str, ...]] = {
    "005_party_first.sql": ("tenant", "artist", "artist_profile"),
}


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

    sql = path.read_text()
    must_be_empty = DESTRUCTIVE.get(path.name, ())
    carried = CARRIED.get(path.name, ())

    # A migration that drops something but is not listed above would run with no guard
    # at all, which is the one way this runner can lose data quietly. Catch the
    # mismatch rather than trusting whoever adds the next file to remember.
    drops = re.search(r"\bDROP\s+(TABLE|COLUMN|DATABASE)\b", sql, re.IGNORECASE)
    if drops and not must_be_empty:
        print(f"REFUSING: {path.name} contains {drops.group(0).upper()} but has no "
              f"entry in DESTRUCTIVE, so nothing checks what it would destroy.\n"
              f"Add its dropped tables to that table, or use an explicit empty tuple "
              f"if the drop is genuinely safe.")
        return 1

    with psycopg.connect(url, autocommit=True) as conn:
        if carried:
            print("-- carried over --")
            for table in carried:
                print(f"  {table:20s} {count(conn, table)}")

        if must_be_empty:
            print("-- must be empty --")
            blocked = []
            for table in must_be_empty:
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
        else:
            print(f"-- {path.name} is additive; no emptiness guards apply --")

        if check_only:
            print("\nguards pass — rerun without --check to apply")
            return 0

        stmts = statements(sql)
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
