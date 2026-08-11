#!/usr/bin/env bash
# Vendor the Essentia model weights into ./models for the Docker build.
#
# Not committed: 20MB of binary weights in git history is 20MB in every clone forever,
# and these are published at a stable URL by the people who trained them. Run once.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$here/models"
cd "$here/models"

base_fx="https://essentia.upf.edu/models/feature-extractors/discogs-effnet"
base_head="https://essentia.upf.edu/models/classification-heads/genre_discogs400"

for url in \
  "$base_fx/discogs-effnet-bs64-1.pb" \
  "$base_head/genre_discogs400-discogs-effnet-1.pb" \
  "$base_head/genre_discogs400-discogs-effnet-1.json"; do
  name="$(basename "$url")"
  if [ -f "$name" ]; then echo "  have $name"; continue; fi
  curl -sS -fL --max-time 300 -O "$url"
  echo "  got  $name"
done

# The embedding graph is 18MB and the head is 2MB. A truncated download produces a
# graph that fails to load with a protobuf parse error, so the sizes are checked here
# where the fix is obvious rather than at image build.
for f in discogs-effnet-bs64-1.pb genre_discogs400-discogs-effnet-1.pb; do
  size=$(stat -c%s "$f")
  if [ "$size" -lt 1000000 ]; then
    echo "REFUSING: $f is only $size bytes — that is a truncated or error-page download"
    exit 1
  fi
done
echo "models ready ($(du -sh . | cut -f1))"
