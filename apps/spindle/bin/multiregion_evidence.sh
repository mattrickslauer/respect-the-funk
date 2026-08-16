#!/usr/bin/env bash
#
# Capture the residency proof from the throwaway three-region cluster, to a file that
# outlives it.
#
# `docs/runbooks/multiregion.md` is the procedure and this is not a replacement for it —
# read it first, particularly §0's two gates and §9's mandatory teardown. This script
# runs only §6, the proof, and exists because of a gap the runbook has by construction:
#
#     §9 deletes the cluster. Everything §6 demonstrates then stops being checkable, and
#     what is left is a person's recollection of some psql output. The submission's whole
#     standing is that its claims are executed rather than asserted — so the one demo
#     whose subject is destroyed at the end needs its evidence written down while the
#     subject still exists.
#
# So: run this between §6 and §9. It writes a timestamped transcript to
# `docs/evidence/`, showing each query and its real output, and it **exits non-zero if a
# proof does not hold**. A transcript that recorded a failure as though it were a result
# would be worse than no transcript, because it would be citable.
#
#     export DATABASE_URL='postgresql://…throwaway cluster…'
#     apps/spindle/bin/multiregion_evidence.sh
#
# It refuses to run against a single-region cluster and refuses to run against the
# production one, for the reason §0 gives: `respect-the-funk` holds the system of record
# and a region cannot be removed from a CockroachDB Cloud cluster once added.

set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL to the throwaway three-region cluster, not to production}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="$REPO/docs/evidence"
OUT="$OUT_DIR/multiregion-$(date -u +%Y-%m-%d).txt"

#: The production cluster, named so this cannot be run against it by a paste error. The
#: guard is on the cluster's own identity rather than on the URL string, because a URL
#: can be spelled several ways and an ID cannot.
PROD_CLUSTER_ID='ae38b92e-c1ad-4a06-a247-489cd5ce9964'

fail() { printf '\n  REFUSED: %s\n\n' "$1" >&2; exit 2; }

if [[ "$DATABASE_URL" == *"$PROD_CLUSTER_ID"* ]]; then
    fail "that is the production cluster (${PROD_CLUSTER_ID}). This script is for the
  throwaway cluster from docs/runbooks/multiregion.md §2. Adding a region to a
  CockroachDB Cloud cluster cannot be undone."
fi

regions="$(psql "$DATABASE_URL" -t -A -c 'SELECT count(*) FROM [SHOW REGIONS]')"
if [[ "$regions" -lt 3 ]]; then
    fail "SHOW REGIONS returns ${regions} region(s); this proof needs the three-region
  cluster from §2. Nothing was written — a transcript from a single-region cluster would
  record the absence of the thing it claims to demonstrate."
fi

mkdir -p "$OUT_DIR"

#: Every proof is (label, SQL). Run in order, each one's real output transcribed under
#: the statement that produced it, so a reader can re-run any line themselves.
run() {
    local label="$1" sql="$2"
    {
        printf '\n%s\n%s\n%s\n\n' "$(printf '=%.0s' {1..78})" "$label" \
            "$(printf '=%.0s' {1..78})"
        printf '$ psql "$DATABASE_URL" -c "%s"\n\n' "$sql"
    } >> "$OUT"
    if ! psql "$DATABASE_URL" -c "$sql" >> "$OUT" 2>&1; then
        printf '\n  PROOF FAILED: %s — see %s\n' "$label" "$OUT" >&2
        return 1
    fi
}

#: §6b is the one proof that passes by *failing*: an unplaceable country must be refused
#: rather than defaulted. It is run separately because success here is a non-zero exit
#: from psql, and `run` would report that as a broken proof.
run_expect_refusal() {
    local label="$1" sql="$2"
    {
        printf '\n%s\n%s\n%s\n\n' "$(printf '=%.0s' {1..78})" "$label" \
            "$(printf '=%.0s' {1..78})"
        printf '$ psql "$DATABASE_URL" -c "%s"\n\n' "$sql"
        printf '  (this statement MUST fail; a success here is the bug)\n\n'
    } >> "$OUT"
    if psql "$DATABASE_URL" -c "$sql" >> "$OUT" 2>&1; then
        printf '\n  PROOF FAILED: %s was ACCEPTED. An unplaceable contact was given a\n' \
            "$label" >&2
        printf '  home rather than being refused — which is the exact failure the\n' >&2
        printf '  NOT NULL on residency_region exists to prevent.\n' >&2
        return 1
    fi
    printf '\n  refused, as required.\n' >> "$OUT"
}

{
    printf 'Three-region residency — evidence transcript\n'
    printf 'Captured %s by apps/spindle/bin/multiregion_evidence.sh\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')"
    printf 'Procedure: docs/runbooks/multiregion.md §6\n\n'
    printf 'This cluster is a throwaway provisioned for this proof and deleted at §9.\n'
    printf 'It is NOT respect-the-funk, which remains single-region — see the guard in\n'
    printf 'this script and the reasoning in the runbook §0 and §7.\n'
} > "$OUT"

run "§0  The cluster this is, and the regions it has" \
    'SELECT region, zones FROM [SHOW REGIONS]'

run "§4  The table's locality — REGIONAL BY ROW, on a derived column" \
    'SHOW CREATE TABLE contact_route'

run "§6a The row knows where it lives (nothing in the INSERT said a region)" \
    'SELECT value, contact_country, residency_region FROM contact_route ORDER BY 2'

run_expect_refusal "§6b An unplaceable contact is refused, not defaulted (CN)" \
    "INSERT INTO contact_route (tenant_id, party_id, channel, value, provenance, source, contact_country)
     SELECT t.id, p.id, 'email', 'md@demo-cn.invalid', 'asserted', 'residency-demo', 'CN'
       FROM tenant t JOIN party p ON p.tenant_id = t.id
      WHERE t.slug = 'residency-demo' AND p.slug = 'demo-station-us'"

run "§6c The storage layer honours it — EU partition" \
    'SHOW ZONE CONFIGURATION FROM PARTITION "aws-eu-west-1" OF TABLE contact_route'

run "§6c The storage layer honours it — US partition, for contrast" \
    'SHOW ZONE CONFIGURATION FROM PARTITION "aws-us-east-1" OF TABLE contact_route'

#: §6d reads the KV layer, which on a Standard cluster is shared infrastructure a tenant
#: may not be permitted to see. The runbook says plainly: *"6c is the proof; 6d is the
#: bonus. Do not rehearse the demo around 6d."* So its failure is recorded and does not
#: fail the run — anything else would make the transcript hostage to a permission.
{
    printf '\n%s\n§6d Physical ranges — BONUS, may be unavailable on a Standard cluster\n%s\n\n' \
        "$(printf '=%.0s' {1..78})" "$(printf '=%.0s' {1..78})"
} >> "$OUT"
if psql "$DATABASE_URL" -c \
    'SELECT start_key, replica_localities, lease_holder_locality
       FROM [SHOW RANGES FROM TABLE contact_route WITH DETAILS]' >> "$OUT" 2>&1; then
    printf '\n  available.\n' >> "$OUT"
else
    printf '\n  NOT AVAILABLE on this cluster, which the runbook §6d predicts and\n' >> "$OUT"
    printf '  explicitly does not rest the argument on. §6c above is the proof.\n' >> "$OUT"
fi

printf '\n\nEnd of transcript.\n' >> "$OUT"

printf '\n  evidence written to %s\n' "${OUT#"$REPO"/}"
printf '  every proof above held. Now do §9 — teardown is mandatory and billed by the hour.\n\n'
