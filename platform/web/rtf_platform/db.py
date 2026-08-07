"""One connection, reused across warm Lambda invocations.

A connection pool is the wrong shape here. Lambda gives each container exactly one
concurrent request, so a pool of N would hold N-1 idle connections open against a
cluster billed by resource unit. One connection, reopened when it has died, matches
the execution model.

`autocommit` is on and transactions are opened explicitly where they are needed.
CockroachDB is SERIALIZABLE by default, so a transaction that touches two tables
gets that isolation without asking — which is the reason PLATFORM-SPEC §1 gives for
this database existing at all.
"""

from __future__ import annotations

import threading

import psycopg
from psycopg.rows import dict_row

_lock = threading.Lock()
_conn: psycopg.Connection | None = None


def connect(database_url: str) -> psycopg.Connection:
    global _conn
    with _lock:
        if _conn is not None and not _conn.closed:
            return _conn
        _conn = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            # A cold Lambda behind a dead connection should fail fast and retry,
            # not hold the request open until the client gives up.
            connect_timeout=10,
            application_name="rtf-platform",
        )
        return _conn


def reset() -> None:
    """Drop the cached connection. Called when a query fails on a stale socket."""
    global _conn
    with _lock:
        if _conn is not None and not _conn.closed:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001 - closing a broken socket is best-effort
                pass
        _conn = None
