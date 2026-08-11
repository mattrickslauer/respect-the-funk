#!/usr/bin/env bash
# Build the classifier image and prove it still recognises six records.
#
#     ./tests/validate.sh
#
# This is the gate on the image, not a nice-to-have. §3a of
# docs/2026-08-10-masters-and-classification.md is the record of a genre classifier that
# was confidently wrong about every track and had nothing to say so. Do not push an
# image that has not passed this.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

engine="${CONTAINER_ENGINE:-podman}"
image="${IMAGE:-rtf-classifier:validate}"

[ -f models/discogs-effnet-bs64-1.pb ] || ./fetch_models.sh
[ -n "$(ls refs/*.mp3 2>/dev/null)" ]  || ./fetch_refs.sh

echo "== building $image =="
"$engine" build -t "$image" .

echo "== reference tracks =="
# --entrypoint is overridden because the image's CMD is the Lambda handler: without this
# the container starts the runtime interface client and waits for an invocation instead
# of running the tests.
"$engine" run --rm \
  -v "$here/refs:/refs:ro,z" \
  -v "$here/tests:/tests:ro,z" \
  -e REFS_DIR=/refs \
  --entrypoint python \
  "$image" /tests/validate.py
