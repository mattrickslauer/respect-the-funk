#!/usr/bin/env bash
# Fetch the reference tracks the classifier is validated against.
#
# Six records spanning six Discogs parent genres, chosen so a front end that collapses
# everything into one genre — which is exactly what the numpy attempt did — cannot pass.
# They are 30-second previews from Deezer's public API: somebody else's copyright,
# fetched for validation and never committed or redistributed.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$here/refs"
python3 - "$here/refs" <<'PY'
import json, sys, urllib.parse, urllib.request
from pathlib import Path

out = Path(sys.argv[1])
REFS = [
    ("daft-punk-around-the-world",  "Daft Punk Around The World"),
    ("metallica-master-of-puppets", "Metallica Master of Puppets"),
    ("coltrane-giant-steps",        "John Coltrane Giant Steps"),
    ("marley-jamming",              "Bob Marley Jamming"),
    ("dre-still-dre",               "Dr. Dre Still D.R.E."),
    ("johnny-cash-folsom",          "Johnny Cash Folsom Prison Blues"),
]
for slug, query in REFS:
    target = out / f"{slug}.mp3"
    if target.exists():
        print(f"  have {slug}")
        continue
    url = "https://api.deezer.com/search?q=" + urllib.parse.quote(query)
    with urllib.request.urlopen(url, timeout=30) as r:
        hits = json.load(r).get("data", [])
    hit = next((t for t in hits if t.get("preview")), None)
    if not hit:
        raise SystemExit(f"REFUSING: Deezer returned no playable preview for {query!r}")
    with urllib.request.urlopen(hit["preview"], timeout=60) as r:
        target.write_bytes(r.read())
    print(f"  got  {slug:30} {hit['artist']['name']} — {hit['title']}")
PY

# Two synthetic fixtures, generated here rather than inside the image.
#
# They test the floors — silence must be refused rather than labelled, and a clip too
# short for the patch window must raise — and both need ffmpeg to create. ffmpeg is
# deliberately not in the runtime image (Essentia links its own decoders), so generating
# them at validation time inside the container is not possible and the tests skipped
# silently instead. Two skipped tests that look like passes is how a floor stops being
# tested at all.
if command -v ffmpeg >/dev/null 2>&1; then
  [ -f "$here/refs/_silence.wav" ] || ffmpeg -v error -y -f lavfi \
    -i anullsrc=r=16000:cl=mono -t 10 "$here/refs/_silence.wav"
  [ -f "$here/refs/_tooshort.wav" ] || ffmpeg -v error -y -f lavfi \
    -i "sine=frequency=440:r=16000" -t 1 "$here/refs/_tooshort.wav"
  echo "  synthesised _silence.wav and _tooshort.wav"
else
  echo "  WARNING: no ffmpeg here, so the floor tests will skip. Install it and rerun."
fi
