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
# this header argues against, and `platform/web/tests/conftest.py` refuses to run
# against anything else. The reasoning and the one command that creates it are in
# `docs/runbooks/environments.md`. Nothing about *this* script changes: the console
# runs against production on purpose, watched by the person who started it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$here/../.."

if [[ -f "$root/.env" ]]; then
  set -a; . "$root/.env"; set +a
fi

: "${DATABASE_URL:?set DATABASE_URL in .env}"
export PLATFORM_ADMIN_TOKEN="${PLATFORM_ADMIN_TOKEN:-dev}"

cd "$here"
exec .venv/bin/uvicorn rtf_platform.main:app --reload --port "${PORT:-8099}"
