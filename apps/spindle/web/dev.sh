#!/usr/bin/env bash
# Run the console locally against the real cluster.
#
# Reads DATABASE_URL from the repository-root .env, which is gitignored. There is
# no local database to run instead: CockroachDB Basic scales to zero and costs
# nothing idle, so developing against the real cluster is cheaper and more honest
# than maintaining a second one that drifts.
#
# That argument still holds, and it stops at the test suite. `pytest` writes tenants,
# leads and campaigns unattended, several hundred times a run, and its output says
# nothing about which cluster it did that to — so the suite gets the second cluster
# this header argues against, and `apps/spindle/web/tests/conftest.py` refuses to run
# against anything else. The reasoning and the one command that creates it are in
# `docs/runbooks/environments.md`. Nothing about *this* script changes: the console
# runs against production on purpose, watched by the person who started it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Three levels, not two: this script sits at `apps/spindle/web/`, so the repository root
# is one further up than it was when the tree was `platform/web/`. Getting this wrong
# does not error — it silently resolves to `apps/`, finds no `.env` there, and the run
# dies on the DATABASE_URL check below with nothing pointing at the cause.
root="$here/../../.."

if [[ -f "$root/.env" ]]; then
  set -a; . "$root/.env"; set +a
fi

: "${DATABASE_URL:?set DATABASE_URL in .env}"
export PLATFORM_ADMIN_TOKEN="${PLATFORM_ADMIN_TOKEN:-dev}"

cd "$here"

# uvicorn lives in requirements-dev.txt, not requirements.txt — see the header of that
# file for why. So a venv built with `pip install -r requirements.txt` looks complete,
# imports the app fine, and fails here with bash's "No such file or directory", which
# names the missing binary and not the install that omitted it. Say which install.
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "dev.sh: no .venv/bin/uvicorn — this venv was built without the dev extras." >&2
  echo "        .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi

exec .venv/bin/uvicorn spindle.main:app --reload --port "${PORT:-8099}"
