# Environments — which cluster is which, and why the test suite gets its own

There are two CockroachDB clusters in this project's design and, until this runbook was
written, one in existence. This document says what each is for, why the split stops
exactly at the test suite, and gives the commands that create the second one.

**The short version.** Production is `respect-the-funk-31317`
(`ae38b92e-c1ad-4a06-a247-489cd5ce9964`). Development runs against it on purpose. The
test suite is forbidden from it by `platform/web/tests/conftest.py`, and the only way to
run the cluster-gated tests is to point `DATABASE_URL` at a second cluster that carries
an `rtf_test_cluster` table.

---

## 1. Why development against production is correct, and testing against it is not

`platform/web/dev.sh` states the position in its header, and it is right:

> There is no local database to run instead: CockroachDB Basic scales to zero and costs
> nothing idle, so developing against the real cluster is cheaper and more honest than
> maintaining a second one that drifts.

Both halves of that hold. *Cheaper* is literal — a Basic cluster with no traffic bills
nothing, so a second cluster kept alive to be a copy of the first costs the same as the
first and buys a second thing to keep in sync. *More honest* is the stronger claim: a
local Postgres or a `cockroach demo` would have different vector-index behaviour,
different `AS OF SYSTEM TIME` semantics and no request-unit budget, so a query that
passes locally tells you nothing about the query that runs. `radiobrowser.py`'s page size
of 100 was chosen because 500 could not commit inside a lease *on this cluster*. You do
not discover that against a substitute.

None of that argument is about the test suite, and the reason is a difference in kind
rather than degree:

|  | `dev.sh` | `pytest` |
|---|---|---|
| Who is watching | a person, on the screen, right now | nobody |
| What it writes | what that person asked for | tenants, parties, leads, campaigns, outbox rows |
| How often | when a page is loaded | 215 tests, several hundred writes, about a second |
| What it does afterwards | leaves it there | drops the tenants it made, if the run got that far |
| What the output says about which cluster | the URL bar | nothing at all |

The last row is the one that matters. A green bar carries no evidence of where it
happened. `test_tenant_scoping.py`, `test_lease_race.py` and `test_integrity_constraints.py`
create tenants and destroy them; a run that dies halfway leaves the wreckage behind, in
the database the deployed console reads. And an interrupted run is not hypothetical — it
is what happens every time someone hits Ctrl-C because a test hung on a cluster that was
scaling up from zero.

So the split is not "dev is dangerous". It is: **unattended destructive writes need a
disposable target, and nothing else does.**

---

## 2. What identifies a cluster as disposable

A table called `rtf_test_cluster`, in the database the connection string names. The
argument for that signal over a hostname denylist or an opt-in environment variable is in
`platform/web/tests/conftest.py`'s header and is not repeated here; the operational
consequence is short:

* A cluster with the marker is a legal test target.
* A cluster without it is refused, whatever it is called and whoever created it.
* If the guard cannot reach the cluster to find out, it refuses. It never falls back to
  skipping the database tests, because a suite that goes green having silently run 215
  fewer tests is the failure this whole arrangement exists to prevent.

There is no flag that turns the guard off.

---

## 3. Creating the test cluster

Prerequisites: `ccloud` (0.8.23 is what this was written against;
`platform/bin/ccloud-mcp-setup.sh` installs it) and a CockroachDB Cloud login.

```bash
ccloud auth login          # OAuth; no API key lands in a file
ccloud cluster list        # confirm you are in the right organisation
```

### 3a. Create it

Same cloud and region as production, so that latency and region-scoped behaviour match
what the code will meet in the deployed function:

```bash
ccloud cluster create BASIC respect-the-funk-tests us-east-1 \
    --cloud AWS \
    --request-unit-limit 5000000 \
    --storage-gib-limit 1 \
    --wait
```

**The two limits are the point of this command, not decoration.** `COSTS.md` rule 7 —
no resource that bills while idle — and rule 3 — ceilings live in configuration, so
raising one shows up in a diff — apply to a cluster exactly as they apply to a token API.
A Basic cluster with no limits set is a cluster that will keep serving after the
organisation's free allowance is gone, and bill for it. 5M request units and 1 GiB are
far above what this suite consumes and far below anything that matters against a $10
project budget.

> **The free allowance is per organisation, not per cluster.** Production already draws
> on it. Adding a second cluster does not add a second allowance, so the honest statement
> is that the test cluster is free *while the total stays under it* — which is why the
> per-cluster limits above are set rather than assumed. Check the current allowance in
> the Cloud console's billing page rather than trusting a number written down here; it
> has changed before.

`--wait` blocks until the cluster is real. Without it the next command runs against a
cluster that does not exist yet, and the error it gives is not obviously that.

### 3b. Make a SQL user

```bash
ccloud cluster user create respect-the-funk-tests tests
```

`ccloud` prints a generated password once. It is not recoverable — if you lose it, run
the same command again to reset it.

### 3c. Get the connection string

```bash
ccloud cluster connection-string respect-the-funk-tests --sql-user tests
```

That prints a URL with a `<password>` placeholder. Substitute the password from 3b.

### 3d. Apply the schema

Every migration, in order, against the new cluster:

```bash
export DATABASE_URL='<the URL from 3c, with the password>'
for f in platform/schema/0*.sql; do
    python platform/schema/apply.py "$(basename "$f")" || break
done
```

`apply.py` refuses any migration containing a `DROP` that is not declared in its
`DESTRUCTIVE` table, so a broken ordering stops rather than eats something. `|| break`
is there because a failure at migration 9 makes migrations 10 through 22 meaningless,
and running them anyway produces a schema nobody can reason about.

### 3e. Declare it disposable

This is the step that makes the suite willing to run:

```sql
CREATE TABLE rtf_test_cluster (
    declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    declared_by STRING NOT NULL,
    note        STRING NOT NULL
);

INSERT INTO rtf_test_cluster (declared_by, note) VALUES (
    'your-name',
    'Disposable. platform/web/tests creates and drops tenants here on every run. '
    'Nothing in this cluster is a record of anything. Safe to delete and recreate.'
);
```

via `ccloud cluster sql respect-the-funk-tests --sql-user tests`.

The row is never read by the guard — only the table's existence is, so that a test which
truncates the world does not revoke its own permission. The row is for the person who
connects to this cluster in six months and needs to know whether they can drop it.

---

## 4. The environment split

Three files, and the rule is that **`DATABASE_URL` is set in exactly one of them at a
time in any given shell.**

| File | Holds | Read by | Committed? |
|---|---|---|---|
| `.env` | production `DATABASE_URL`, every API key | `dev.sh`, the workers, `apply.py` | no — gitignored |
| `.env.tests` | the test cluster's `DATABASE_URL`, and nothing else | you, before `pytest` | no — gitignored by the existing `.env.*` rule |
| `.env.example` | the shape, no values | humans | yes |

`.env.tests` is a new file and deliberately holds one line:

```bash
# The test cluster. NOT production — see docs/runbooks/environments.md.
# platform/web/tests/conftest.py refuses to run against anything without an
# rtf_test_cluster table, which this cluster has and production does not.
export DATABASE_URL="postgresql://tests:<password>@respect-the-funk-tests-XXXXX.j77.aws-us-east-1.cockroachlabs.cloud:26257/defaultdb?sslmode=verify-full"
```

**Do not put the test URL in `.env`.** `.env` is what `dev.sh` sources, so a test URL
there silently points the console at a database full of test wreckage — the same class of
mistake as this runbook's subject, pointed the other way.

### Running the suite

The full suite, against the test cluster:

```bash
set -a; . .env.tests; set +a
cd platform/web && .venv/bin/python -m pytest -q
```

The database-free suite — 232 tests, no cluster, no network, and the one to run when you
just want to know whether the code compiles and the logic holds:

```bash
cd platform/web && env -u DATABASE_URL .venv/bin/python -m pytest -q
```

`env -u` rather than `unset`, so it is scoped to the one command and cannot leave a shell
in a state where the next thing you run has lost its database.

---

## 5. What CI does

`.github/workflows/tests.yml` runs the database-free suite and nothing else, with no
secrets configured at all. That is a deliberate limit, not an oversight, and the workflow
file argues it: a repository secret holding a database password is a password that every
workflow in the repository can read, including one added by a pull request, and the
232 tests that need no cluster are the ones that catch the mistakes CI is good at
catching. Wiring CI to the test cluster is a decision to make on purpose, with an
environment-scoped secret and a required reviewer, and the workflow says what that would
take.

---

## 6. Deleting and recreating the test cluster

It is disposable — that is the whole claim — so treat it that way when it gets strange:

```bash
ccloud cluster delete respect-the-funk-tests
```

Then repeat §3. Nothing in it is a record of anything, and this is the only cluster in
the project that statement is true of.

Note that recreating it produces a **new host** with a new numeric suffix, and
`.env.tests` must be updated. Nothing else changes: the guard identifies the cluster by
its marker table, not by its name, so a recreated test cluster works the moment §3e runs.
That is the property a hostname allow-list would not have had.
