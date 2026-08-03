# The demo video

Everything the submission video is cut from. It was on a Desktop; it is here now.

| | |
|---|---|
| `SCRIPT.md` | The shooting script — timings, shot list, and the fact-check of every claim against the code. Read this first. |
| `screenshots/` | Ten console pages, captured at 2×. Committed against the repo's `*.png` rule: they are one tenant's data at one moment, and the edit is cut to these exact frames. |
| `broll/` | Six real assets from real runs. **Not committed** — every file already exists under `runs/`. `broll/SOURCES.md` says where. |
| `video/` | Builds the generated half of the video: voice-over, picture track, encode. `video/README.md`. |

## The two halves

The video is the founder on camera for the cold open, then a generated read of the rest.
The handover is the line `Register an artist` in `docs/demo-voiceover.txt` — which is the
single copy of the words, and what `video/vo.py` slices from.

`video/build.py` produces the second half only, 120.0s of it. The first half is filmed.

## Before you re-shoot any screenshot

Run the console against B2, not local storage. On local storage the generate page refuses
face conditioning and the two-stage Genblaze chain collapses to one call — the beat the
script is built around disappears. `SCRIPT.md` records which of the ten were captured on
which backend.
