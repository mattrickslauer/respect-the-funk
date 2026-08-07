#!/usr/bin/env bash
# Run the console locally against the real cluster.
#
# Reads DATABASE_URL from the repository-root .env, which is gitignored. There is
# no local database to run instead: CockroachDB Basic scales to zero and costs
# nothing idle, so developing against the real cluster is cheaper and more honest
# than maintaining a second one that drifts.
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
