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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import certifi
import psycopg
from psycopg.rows import dict_row

_lock = threading.Lock()
_conn: psycopg.Connection | None = None


def with_ca_bundle(url: str) -> str:
    """Point `sslrootcert` at a CA bundle we ship, when the URL does not name one.

    CockroachDB Cloud hands out a connection string ending `?sslmode=verify-full`
    and nothing else, and its certificate is issued by Let's Encrypt — a public CA,
    so on a developer machine libpq finds a root in the system store and it simply
    works. In Lambda it does not, twice over:

      * libpq's default is `~/.postgresql/root.crt`, and the sandbox home is empty,
        which fails with "root certificate file does not exist";
      * `sslrootcert=system` gets past that but then fails verification, because
        psycopg[binary] bundles its own OpenSSL whose compiled-in CA path is the
        one from *its* build machine and does not exist here.

    certifi is the same root store the rest of the Python ecosystem trusts, it
    contains ISRG Root X1, and it travels inside the zip. Fixing it here rather
    than in the connection string means the value pasted from CockroachDB's console
    works unmodified in both places, which is the version nobody has to remember.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if query.get("sslmode", "").startswith("verify") and not query.get("sslrootcert"):
        query["sslrootcert"] = certifi.where()
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def connect(database_url: str) -> psycopg.Connection:
    global _conn
    with _lock:
        if _conn is not None and not _conn.closed:
            return _conn
        _conn = psycopg.connect(
            with_ca_bundle(database_url),
            autocommit=True,
            row_factory=dict_row,
            # A cold Lambda behind a dead connection should fail fast and retry,
            # not hold the request open until the client gives up.
            connect_timeout=10,
            application_name="spindle",
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
