"""The suite is not allowed to touch the production cluster. This file is the fence.

`dev.sh`, the worker and this suite all read one `DATABASE_URL`, and in a configured
checkout it points at `respect-the-funk-31317` — the cluster the deployed console
serves from. `dev.sh` argues that this is *correct*, and for development it is:
CockroachDB Basic scales to zero, so a second cluster kept alive only to be a copy of
the first costs the same and drifts. That argument is sound and it is not being
overturned here. It just stops being sound at the boundary of this directory.

Developing against production is a read-mostly activity performed by a human who is
looking at the screen. A test run is neither. `test_tenant_scoping.py` and
`test_lease_race.py` create tenants, write leads and campaigns against them and drop
them again — unattended, on every invocation. **215 of the tests here are gated on
`DATABASE_URL` being set, and every one of them assumes it may make and destroy rows.**
Run `pytest` in a shell that has sourced `.env` and you have run a program that mutates
the production database several hundred times in about a second and then printed a green
bar. Nothing in that output says which cluster it happened to. That is the whole gap.

The fence is not "be careful". It is: the suite refuses to start against a cluster that
has not *proved* it is disposable.


## The identifying signal, and why it is a table rather than a hostname or an env var

Three candidates. Two of them fail, and the failures are worth recording because they
are the obvious answers.

**The CockroachDB Cloud cluster id — `ae38b92e-c1ad-4a06-a247-489cd5ce9964` — cannot be
read over SQL, measured rather than assumed.** That id is what `ccloud cluster list` and
the Cloud console show, so it looks like the durable identity to check. It is not
reachable from a SQL session on a Basic cluster. Against this cluster on 2026-08-13:

    SELECT crdb_internal.cluster_id()
      -> ERROR: Access to crdb_internal and system is restricted

    SET allow_unsafe_internals = true; SELECT crdb_internal.cluster_id()
      -> 9fad7a1e-e440-4989-3831-6d191b8c2f02

    SHOW CLUSTER SETTING cluster.organization
      -> 'Cockroach Cloud - crl-prod-j77'

The UUID that comes back is the virtual cluster's, not the Cloud cluster's — a different
value from the one a human can see anywhere — and reading it at all needs a session
variable whose own hint says the interface is unsupported. `cluster.organization` names
the CockroachDB Cloud *host region*, which every cluster in `j77` shares. So "ask the
cluster who it is" has no answer here that a human could verify or maintain. The id is
still recorded below, because it is the identity a human uses in `ccloud`, and a stale
constant that nobody can check against anything is worse than one that names the thing
in the console.

**A hostname denylist fails open, which is the wrong direction to fail.** Matching
`respect-the-funk-31317.j77.aws-us-east-1.cockroachlabs.cloud` catches today's accident
and nothing else. CockroachDB Cloud hosts do not get renamed, so the worry is not a
rename — it is that production gets *recreated*, gets a new numeric suffix, and this
constant silently stops matching anything. A denylist that has gone stale is not a
weaker guard, it is a guard that says yes to production. It is kept below, but only as a
fast path that can *refuse* before opening a connection; it is never consulted to permit.

**An opt-in environment variable is bypassable by exactly the accident it is for.**
`RTF_TESTS_MAY_WRITE=1` is one `export` in a `.bashrc` away from being permanently true
in the shell where the developer also sources `.env`. The variable then authorises
whatever `DATABASE_URL` happens to be, forever, silently. An env var is a property of the
shell; the thing that needs identifying is the cluster.

**So the signal is a marker table that lives in the cluster itself.** A cluster is a
legitimate test target if and only if it contains a table called `rtf_test_cluster`.
This is a positive allow-list, and its two properties are the ones that matter:

  * **Production cannot acquire it by accident.** Nobody types `CREATE TABLE
    rtf_test_cluster` into the production database by mistake, and if the name ever did
    appear there, the name is itself the alarm.
  * **It survives everything the other two signals do not** — a recreated cluster, a new
    host, a second test cluster, a different developer's account. Whoever creates a test
    cluster follows `docs/runbooks/environments.md`, which creates the marker in the same
    breath as the database.

The cost is one connection per `pytest` invocation that has `DATABASE_URL` set, spent on
a single `information_schema` read. It is the same connection those tests were about to
open anyway, and when `DATABASE_URL` is unset — the documented way to run the DB-free
suite — nothing is opened at all.


## No fallbacks, in the specific sense that matters here

A guard that skips when it cannot work out what it is looking at is worse than no guard,
because it reads as protection. Every uncertain outcome below therefore refuses:

  * cannot connect, wrong password, TLS failure, DNS failure, timeout — **refuse**
  * connected, but no `rtf_test_cluster` — **refuse**
  * anything else raised while probing — **refuse**, with the underlying error printed

In particular the guard never degrades into "skip the database tests and run the rest".
Silently skipping 215 tests is how a suite goes green while testing nothing, and the
whole point of this file is that a green bar should mean what it says. If you want the
DB-free suite, ask for it explicitly by not setting `DATABASE_URL`.

There is deliberately **no override flag**. An escape hatch that exists is an escape
hatch that ends up in someone's shell profile, and then this file is decoration.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Callable

import pytest

#: The production cluster's CockroachDB Cloud id. Not used by any check — it *cannot*
#: be, see the header — and recorded here because it is the identity `ccloud cluster
#: list`, the Cloud console and `.mcp.json` all use, so a human reading a refusal
#: message can confirm which cluster is being talked about without guessing from a
#: hostname. If production is ever rebuilt, this and `PRODUCTION_SQL_HOST` both change,
#: and only this one is checkable by a person.
PRODUCTION_CLUSTER_ID = "ae38b92e-c1ad-4a06-a247-489cd5ce9964"

#: The production cluster's SQL host, as it appears in `.env`. A fast path only: it lets
#: the overwhelmingly common accident — a shell with `.env` sourced — fail in
#: milliseconds without opening a connection to the very cluster it is protecting.
#: Compared as a whole hostname, not as a substring, because substring matching on a
#: name that contains the project's own name would also catch a test cluster called
#: `respect-the-funk-tests-40021`, and refusing the right cluster teaches people to
#: distrust the guard.
PRODUCTION_SQL_HOST = "respect-the-funk-31317.j77.aws-us-east-1.cockroachlabs.cloud"

#: The marker. Its presence is the entire permission model. Created by
#: `docs/runbooks/environments.md` when a test cluster is provisioned; its row records
#: who declared the cluster disposable and when, for the benefit of whoever finds it
#: later and wonders.
MARKER_TABLE = "rtf_test_cluster"

#: Seconds to wait for the probe connection. A CockroachDB Basic cluster that has scaled
#: to zero resumes in a few seconds, so this is generous on purpose: a timeout here
#: refuses the run, and refusing a correct cluster because it was asleep would be a
#: false alarm rather than a caught mistake.
PROBE_TIMEOUT_SECONDS = 20

RUNBOOK = "docs/runbooks/environments.md"


@dataclass(frozen=True)
class Verdict:
    """Whether this run may proceed, and — when it may not — what to do instead.

    `reason` and `remedy` are separate fields rather than one formatted string because
    the test that proves this guard works asserts on the reason, and a test that has to
    match against prose formatted for a terminal will break every time the prose is
    improved.
    """

    allowed: bool
    reason: str
    remedy: str = ""


def sql_host(url: str) -> str | None:
    """The hostname in a connection URL, lowercased, or None if there isn't one.

    None is a normal answer, not an error: `DATABASE_URL` may legitimately be a libpq
    keyword/value string (`host=… port=…`), which is not a URL and which psycopg accepts
    perfectly well. It is not treated as suspicious, because the host check is only ever
    a fast path to refuse — an unparseable URL simply goes on to the probe, which is the
    check that actually decides.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def marker_present(url: str) -> bool:
    """Does this cluster carry the `rtf_test_cluster` marker?

    Existence only; the row inside it is not read. A test that truncates the world would
    empty the table without dropping it, and refusing a perfectly good test cluster
    because a test did its job is a false alarm. Dropping the table *does* revoke the
    cluster's permission, which is the correct way round: the fence fails closed.

    `information_schema` is used rather than `crdb_internal` because the latter is
    restricted on a Basic cluster (measured — see the header) and because
    `information_schema` is scoped to the connected database, which is exactly the
    granularity that matters. Any exception propagates: this function's contract is to
    answer the question or raise, never to guess.
    """
    import psycopg  # local: keeps a collection-time import off the DB-free path

    with psycopg.connect(url, connect_timeout=PROBE_TIMEOUT_SECONDS,
                         autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (MARKER_TABLE,),
            )
            row = cur.fetchone()
    return bool(row and row[0])


def inspect(url: str, probe: Callable[[str], bool] = marker_present) -> Verdict:
    """Decide whether a run against `url` may proceed.

    `probe` is injected so the decision can be tested without a cluster — the guard's own
    test drives every branch through fakes. It is not an extension point and there is no
    way to supply it from the outside.
    """
    url = (url or "").strip()

    # No database configured. The suite runs; the 215 cluster tests skip themselves on
    # exactly this condition, which they already did before this file existed.
    if not url:
        return Verdict(True, "DATABASE_URL is unset — cluster tests will skip.")

    if sql_host(url) == PRODUCTION_SQL_HOST:
        return Verdict(
            False,
            f"DATABASE_URL points at the production cluster "
            f"({PRODUCTION_SQL_HOST}, CockroachDB Cloud id {PRODUCTION_CLUSTER_ID}). "
            f"This suite creates and drops tenants; that is the live database.",
            _remedy(),
        )

    try:
        marked = probe(url)
    except Exception as exc:  # noqa: BLE001 — the message is the product
        return Verdict(
            False,
            f"Could not establish what cluster DATABASE_URL points at, so the run is "
            f"refused rather than risked. {type(exc).__name__}: {exc}",
            _remedy(),
        )

    if not marked:
        return Verdict(
            False,
            f"The cluster DATABASE_URL points at has no `{MARKER_TABLE}` table, so it "
            f"has not been declared disposable. It may be production, or it may be a "
            f"cluster nobody has finished setting up; this guard cannot tell those "
            f"apart and will not assume.",
            _remedy(),
        )

    return Verdict(True, f"Cluster carries the `{MARKER_TABLE}` marker.")


def _remedy() -> str:
    return (
        "Do one of these:\n"
        "\n"
        "  1. Run the suite without a database. The tests that need a cluster skip\n"
        "     themselves, the rest run, and nothing is written anywhere:\n"
        "\n"
        "         cd apps/spindle/web && env -u DATABASE_URL .venv/bin/python -m pytest -q\n"
        "\n"
        "  2. Point it at a test cluster. One `ccloud` command creates a free\n"
        f"     CockroachDB Basic cluster that scales to zero; see {RUNBOOK}.\n"
        "     A cluster becomes a legal test target by carrying the marker table:\n"
        "\n"
        f"         CREATE TABLE {MARKER_TABLE} (\n"
        "             declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
        "             declared_by STRING NOT NULL,\n"
        "             note        STRING NOT NULL\n"
        "         );\n"
        "\n"
        "There is no flag that turns this off. If you believe the guard is wrong, the\n"
        "thing to fix is which cluster DATABASE_URL names."
    )


def pytest_configure(config: pytest.Config) -> None:
    """The hook. Runs once, before any test module is imported.

    Ordering matters and is the reason this is `pytest_configure` and not a fixture:
    the cluster tests read `os.environ["DATABASE_URL"]` at *import* time to decide
    whether to skip, and imports happen during collection, after this hook. A fixture
    would run after the connections it is meant to prevent.

    `pytest.UsageError` rather than a raised exception or `sys.exit`: it prints one
    `ERROR:` block with no traceback and exits non-zero, which is what a person who has
    just made this mistake needs to read.
    """
    verdict = inspect(os.environ.get("DATABASE_URL", ""))
    if verdict.allowed:
        return
    raise pytest.UsageError(
        f"\n\nREFUSING TO RUN.\n\n{verdict.reason}\n\n{verdict.remedy}\n"
    )
