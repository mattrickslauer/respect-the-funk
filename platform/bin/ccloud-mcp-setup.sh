#!/usr/bin/env bash
# Install ccloud, find this project's cluster with it, and write the MCP client config.
#
#     ./platform/bin/ccloud-mcp-setup.sh
#
# Two of the hackathon's four CockroachDB tools are wired up by this one script, and
# neither is wired up by talking about it:
#
#   * **ccloud CLI** — used here to resolve the cluster ID, which is a thing we actually
#     need rather than a command run once to be able to say we ran it. `platform/README.md`
#     previously recorded the honest position: "ccloud has not been used… use it for
#     something real or drop the claim." This is the something real.
#
#   * **Cloud Managed MCP Server** — the config this emits points an MCP client at
#     https://cockroachlabs.cloud/mcp, scoped to that cluster.
#
# Authentication is OAuth, deliberately. A service-account API key would also work and
# would let this run headless, but it would put a bearer token in a file that is
# committed. The cluster ID is an identifier, not a credential; the token is a
# credential, so it stays in the client's own auth store where a `git add -A` cannot
# reach it.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cluster_name="${CCLOUD_CLUSTER:-}"
config="$root/.mcp.json"

# ------------------------------------------------------------------ ccloud present?

if ! command -v ccloud >/dev/null 2>&1; then
  echo "ccloud not found — installing to ~/.local/bin"
  mkdir -p "$HOME/.local/bin"
  tmp="$(mktemp -d)"
  # Downloaded and extracted rather than piped into a shell: `curl … | sh` executes
  # whatever the endpoint returns today, which is not a thing to do unattended.
  curl -sSL -o "$tmp/ccloud.tgz" \
    "https://binaries.cockroachdb.com/ccloud/ccloud_linux-amd64_latest.tar.gz"
  tar xzf "$tmp/ccloud.tgz" -C "$tmp"
  install -m 0755 "$tmp/ccloud" "$HOME/.local/bin/ccloud"
  rm -rf "$tmp"
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "ccloud $(ccloud version | head -1 | awk '{print $2}')"

# ---------------------------------------------------------------------------- auth

if ! ccloud auth whoami >/dev/null 2>&1; then
  echo
  echo "Not logged in. A browser window will open for CockroachDB Cloud."
  ccloud auth login
fi
echo "authenticated as $(ccloud auth whoami 2>/dev/null | tail -1)"

# ------------------------------------------------------------------- the cluster id

if [[ -z "$cluster_name" ]]; then
  # Read from .env if it is there, so this agrees with what the app connects to rather
  # than with whatever happens to be first in the account.
  if [[ -f "$root/.env" ]]; then
    cluster_name="$(grep -E '^CCLOUD_CLUSTER=' "$root/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'' || true)"
  fi
fi

if [[ -z "$cluster_name" ]]; then
  echo
  echo "Which cluster? Set CCLOUD_CLUSTER, or pick from:"
  ccloud cluster list
  exit 1
fi

cluster_id="$(ccloud cluster list -o json \
  | python3 -c "
import json, sys
want = sys.argv[1]
doc = json.load(sys.stdin)
# ccloud 0.8 returns a bare array; older and newer builds have wrapped it in an
# object. Accept either rather than pinning to whichever one is installed today.
clusters = doc if isinstance(doc, list) else doc.get('clusters', [])

# CockroachDB Cloud's connection host carries a numeric label the cluster's own name
# does not — 'respect-the-funk-31317.j77.aws…' is the host for a cluster displayed as
# 'respect-the-funk'. .env holds the host form, because that is what you paste from the
# console, so the exact name is tried first and the stripped form second.
import re
stripped = re.sub(r'-\d+$', '', want)
by_name = {c.get('name'): c.get('id') for c in clusters}
print(by_name.get(want) or by_name.get(stripped) or '')
" "$cluster_name")"

if [[ -z "$cluster_id" ]]; then
  echo "no cluster named '$cluster_name' in this account" >&2
  ccloud cluster list >&2
  exit 1
fi

echo "cluster '$cluster_name' -> $cluster_id"

# ------------------------------------------------------------------- the MCP config

# Merged rather than overwritten: this file is shared with any other MCP server the
# project uses, and clobbering somebody else's entry to add ours is rude and silent.
python3 - "$config" "$cluster_id" <<'PY'
import json, pathlib, sys

path, cluster_id = pathlib.Path(sys.argv[1]), sys.argv[2]
doc = {}
if path.exists():
    doc = json.loads(path.read_text() or "{}")
servers = doc.setdefault("mcpServers", {})
servers["cockroachdb-cloud"] = {
    "type": "http",
    "url": "https://cockroachlabs.cloud/mcp",
    "headers": {"mcp-cluster-id": cluster_id},
}
path.write_text(json.dumps(doc, indent=2) + "\n")
print(f"wrote {path}")
PY

cat <<EOF

Done. Restart your MCP client to pick up .mcp.json, then authorise it when prompted.

The server is read-only by default — it registers only the read tools and forces every
SQL session read-only on top of that. That is the correct posture for this: the console
is the write path, and an agent that can rewrite the spine through a chat prompt is a
worse system than one that cannot.

Try:
  "list the tables in defaultdb"
  "how many chunks are embedded, grouped by model?"
  "explain the query plan for a vector search on party_chunk"
EOF
