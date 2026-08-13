"""Proof that `conftest.py`'s fence actually fences, exercised through fakes.

A guard nobody has seen fire is a guard nobody knows is wired up. This one is
especially easy to get wrong in the direction that looks fine: every mistake it can
make — a probe that raises and is caught into "allowed", a host comparison that never
matches, a hook registered where nothing calls it — produces a *passing* suite, which
is exactly the state the guard exists to prevent. So the branches are tested by name.

**DB-free by construction, for the same reason `test_tenant_scoping.py` is.** The
decision is `conftest.inspect(url, probe)`, whose probe is injected; the fakes here
stand in for a cluster and record whether they were called. That last part is a real
assertion rather than bookkeeping: the production branch must refuse *without opening a
connection*, because a guard whose first act is to dial the cluster it is protecting has
already done the thing it forbids, and on a scaled-to-zero Basic cluster it also wakes
production on every run of a suite that was never meant to reach it.

`conftest` is imported as a plain module. pytest has already imported it under that
name before this file is collected — it is the conftest governing this directory — so
this picks the same module object out of `sys.modules` rather than loading a second copy.
"""

from __future__ import annotations

import ast
import unittest

import conftest


def refusing_probe(url: str) -> bool:
    """A probe that must never be reached. Calling it fails the test that allowed it."""
    raise AssertionError(
        f"the guard opened a connection to {conftest.sql_host(url)}, which the "
        f"host fast path is supposed to make unnecessary"
    )


class Probe:
    """A stand-in cluster: answers the marker question, and remembers being asked."""

    def __init__(self, marked: bool | BaseException):
        self.marked = marked
        self.calls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.calls.append(url)
        if isinstance(self.marked, BaseException):
            raise self.marked
        return self.marked


#: The shape `.env` holds. Not the real password — the guard never looks at credentials,
#: only at the host, which is deliberately the one part of a connection string that is
#: not a secret.
PRODUCTION_URL = (
    f"postgresql://anthony:not-the-real-password@{conftest.PRODUCTION_SQL_HOST}"
    f":26257/defaultdb?sslmode=verify-full"
)

TEST_URL = (
    "postgresql://anthony:not-the-real-password@"
    "respect-the-funk-tests-40021.j77.aws-us-east-1.cockroachlabs.cloud"
    ":26257/defaultdb?sslmode=verify-full"
)


class GuardRefusesProduction(unittest.TestCase):

    def test_production_url_is_refused(self):
        verdict = conftest.inspect(PRODUCTION_URL, refusing_probe)
        self.assertFalse(verdict.allowed)
        self.assertIn(conftest.PRODUCTION_SQL_HOST, verdict.reason)

    def test_production_is_refused_without_connecting_to_it(self):
        """The fast path exists so the accident is cheap and touches nothing.

        `refusing_probe` raises if called. If the host check ever stops matching, this
        test does not merely fail — it fails with the reason it stopped matching.
        """
        conftest.inspect(PRODUCTION_URL, refusing_probe)  # raises if the probe is used

    def test_the_remedy_names_what_to_do_instead(self):
        """A refusal that does not say what to do instead gets worked around, and the
        workaround is somebody deleting this file."""
        verdict = conftest.inspect(PRODUCTION_URL, refusing_probe)
        self.assertIn("env -u DATABASE_URL", verdict.remedy)
        self.assertIn(conftest.MARKER_TABLE, verdict.remedy)
        self.assertIn("docs/runbooks/environments.md", verdict.remedy)

    def test_host_is_matched_whole_not_as_a_substring(self):
        """A test cluster named after the project must not be caught by the denylist.

        `respect-the-funk-tests-40021…` contains `respect-the-funk`, and a substring
        check would refuse it. Refusing the correct cluster is how a guard earns the
        reputation that gets it disabled.
        """
        probe = Probe(marked=True)
        verdict = conftest.inspect(TEST_URL, probe)
        self.assertTrue(verdict.allowed, verdict.reason)
        self.assertEqual(probe.calls, [TEST_URL])


class GuardAllowsADeclaredTestCluster(unittest.TestCase):

    def test_marked_cluster_is_allowed(self):
        verdict = conftest.inspect(TEST_URL, Probe(marked=True))
        self.assertTrue(verdict.allowed, verdict.reason)
        self.assertEqual(verdict.remedy, "")

    def test_no_database_url_runs_the_db_free_suite(self):
        """The documented way to run this suite. Must stay free, and must not probe."""
        for url in ("", "   ", None):
            with self.subTest(url=url):
                verdict = conftest.inspect(url, refusing_probe)
                self.assertTrue(verdict.allowed, verdict.reason)
                self.assertIn("unset", verdict.reason)


class GuardRefusesWhatItCannotIdentify(unittest.TestCase):
    """The no-fallbacks half. Every one of these could plausibly be a skip, and a skip
    is how 215 tests go quiet while the bar stays green."""

    def test_unmarked_cluster_is_refused(self):
        verdict = conftest.inspect(TEST_URL, Probe(marked=False))
        self.assertFalse(verdict.allowed)
        self.assertIn(conftest.MARKER_TABLE, verdict.reason)

    def test_unreachable_cluster_is_refused_not_skipped(self):
        boom = OSError("connection to server failed: Network is unreachable")
        verdict = conftest.inspect(TEST_URL, Probe(marked=boom))
        self.assertFalse(verdict.allowed)
        self.assertIn("Network is unreachable", verdict.reason)

    def test_the_underlying_error_survives_into_the_message(self):
        """Refusing without saying why turns a broken password into a mystery."""
        boom = RuntimeError("password authentication failed for user 'anthony'")
        verdict = conftest.inspect(TEST_URL, Probe(marked=boom))
        self.assertIn("RuntimeError", verdict.reason)
        self.assertIn("password authentication failed", verdict.reason)

    def test_a_url_that_is_not_a_url_still_reaches_the_probe(self):
        """libpq keyword/value strings are a legitimate `DATABASE_URL`, and psycopg
        takes them. They have no parseable host, so the fast path cannot judge them —
        which is fine, because the fast path only ever refuses. The probe decides."""
        kv = "host=somewhere.example port=26257 dbname=defaultdb"
        self.assertIsNone(conftest.sql_host(kv))
        probe = Probe(marked=False)
        self.assertFalse(conftest.inspect(kv, probe).allowed)
        self.assertEqual(probe.calls, [kv])


class GuardIsWiredUp(unittest.TestCase):
    """Testing `inspect` proves the decision. These prove the decision is consulted."""

    def test_pytest_configure_is_the_hook(self):
        self.assertTrue(callable(getattr(conftest, "pytest_configure", None)))

    def test_the_guard_reads_exactly_one_environment_variable(self):
        """`DATABASE_URL`, and nothing else.

        Parsed rather than grepped, the way `test_tenant_scoping.py` parses: the
        conftest's prose argues at length *against* an opt-in variable and names several
        as examples, so any text search over the file matches its own reasoning. The AST
        sees only what runs.

        A second environment read appearing here is the shape every bypass takes —
        `RTF_TESTS_MAY_WRITE`, `SKIP_DB_GUARD`, `CI` — and each one is a change that
        breaks nothing and quietly makes the fence optional.
        """
        tree = ast.parse(open(conftest.__file__).read())
        # All three spellings, not just the one currently used: `os.getenv` is the
        # form a bypass would most naturally be written in, and a check that only
        # knew about `os.environ` would wave it through.
        reads = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"environ", "getenv", "environb"}
            and isinstance(node.value, ast.Name) and node.value.id == "os"
        ]
        self.assertEqual(len(reads), 1,
                         f"{len(reads)} reads of os.environ; expected only DATABASE_URL")

        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertIn("DATABASE_URL", literals)


if __name__ == "__main__":
    unittest.main()
