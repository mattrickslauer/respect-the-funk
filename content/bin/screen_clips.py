#!/usr/bin/env python3
"""Flag library clips carrying burned-in text, captions, logos or watermarks.

    .venv/bin/python bin/screen_clips.py lib/stock/nocturnal            # report
    .venv/bin/python bin/screen_clips.py lib/stock/nocturnal --reroll   # drop + re-fetch

A found-footage library's most visible failure is not a bad shot — it is somebody else's
lower-third. One tutorial frame reading "FAN SETTINGS / TURNING CLOCKWISE" in the middle
of a music video destroys the illusion that the fifteen sources are one video, and no
amount of grading or beat-cutting recovers it. It happened on the first render.

**The signal is temporal, not spatial.** An OCR pass is the obvious idea and the wrong
one: it needs a model, it misses logos and UI chrome, and it fires on legitimate signage
— a neon sign that says CLOSED is exactly the shot we want. What actually separates an
overlay from the world is that an overlay is *pixel-locked while the scene moves*. So:
per-pixel temporal variance, per-pixel edge energy, and flag the pixels that are strongly
edged and completely still.

**It refuses to judge what it cannot judge.** In a locked-off shot every pixel is still,
so the test cannot tell an overlay from a wall and says so rather than guessing. That is
a real coverage gap, stated rather than hidden: static clips are screened by eye.

**How well does it actually work?** On the 15-clip `nocturnal` library: one true positive
(`ceiling-fan`, ratio 0.030 / axis 0.83), eleven clean at ratio ≤ 0.008, three abstained.
Both thresholds are therefore calibrated against **a single positive example**, which is
not enough to claim a false-positive rate. The first version, without the axis test, also
flagged `ocean-night` — a moon over water, no overlay at all. Treat this as triage and
keep looking at the contact sheet, exactly as `check_likeness.py` is treated one layer up.

Requires: ffmpeg, numpy, pyyaml. No API, no cost, deterministic.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

SCREEN_W = 240
SCREEN_FPS = 10
STILL_SD = 3.0        # temporal std below this is "pixel-locked"
EDGE_MIN = 18.0       # gradient above this is "a hard edge", i.e. type or a graphic
MOTION_FLOOR = 2.0    # below this the scene itself is static; the test cannot separate
FLAG_RATIO = 0.010    # share of frame that is locked-and-edged before we call it overlay
AXIS_MIN = 0.75       # share of that mask whose gradients sit on an axis, i.e. is drawn


def decode(path: Path) -> np.ndarray:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True).stdout.decode().strip().split(",")
    w, h = int(out[0]), int(out[1])
    lw = SCREEN_W
    lh = max(2, int(round(lw * h / w / 2)) * 2)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         f"fps={SCREEN_FPS},scale={lw}:{lh}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    n = len(raw) // (lw * lh)
    if n < 3:
        raise RuntimeError("too few frames to screen")
    return np.frombuffer(raw[: n * lw * lh], np.uint8).reshape(n, lh, lw).astype(np.float32)


def screen(path: Path) -> dict:
    f = decode(path)
    motion = float(np.abs(np.diff(f, axis=0)).mean())
    sd = f.std(axis=0)
    gy, gx = np.gradient(f.mean(axis=0))
    edge = np.hypot(gx, gy)

    mask = (sd < STILL_SD) & (edge > EDGE_MIN)
    ratio = float(mask.mean())

    # "Locked and edged" alone is not enough: a full moon over moving water is locked,
    # edged, and entirely legitimate — it was the detector's first false positive. What
    # separates type from a subject is that type is *drawn*: horizontal strokes, vertical
    # stems, rectangular plates, so its gradients pile up on the two axes. A moon is a
    # circle and spreads its gradients over every direction.
    if mask.sum() >= 20:
        ang = np.arctan2(gy[mask], gx[mask]) % (np.pi / 2)
        off = np.minimum(ang, np.pi / 2 - ang)          # radians from the nearest axis
        axis_align = float((off < np.radians(18)).mean())
    else:
        axis_align = 0.0

    if motion < MOTION_FLOOR:
        verdict = "unscreenable"          # nothing moves; an overlay looks like the world
    elif ratio > FLAG_RATIO and axis_align > AXIS_MIN:
        verdict = "overlay"
    else:
        verdict = "clean"
    return {"overlay_ratio": round(ratio, 5), "axis_align": round(axis_align, 3),
            "motion": round(motion, 3), "verdict": verdict,
            "usable": verdict != "overlay"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("library")
    ap.add_argument("--reroll", action="store_true",
                    help="delete flagged clips and blacklist their video ids")
    ap.add_argument("--flag-ratio", type=float, default=FLAG_RATIO)
    args = ap.parse_args()

    lib = Path(args.library)
    sides = sorted(lib.glob("*.clip.yaml"))
    if not sides:
        sys.exit(f"no sidecars in {lib}")

    rejected_path = lib / "rejected.yaml"
    rejected = yaml.safe_load(rejected_path.read_text()) if rejected_path.exists() else []
    rejected = rejected or []

    flagged = []
    print(f"{'clip':<20} {'ratio':>8} {'axisAl':>7} {'motion':>8}  verdict")
    for side in sides:
        doc = yaml.safe_load(side.read_text())
        media = lib / doc["media"]
        if not media.exists():
            continue
        try:
            r = screen(media)
        except Exception as exc:                                # noqa: BLE001
            print(f"{doc['id']:<20} {'-':>8} {'-':>7} {'-':>8}  ERROR {exc}")
            continue
        if (r["overlay_ratio"] > args.flag_ratio and r["axis_align"] > AXIS_MIN
                and r["verdict"] != "unscreenable"):
            r["verdict"], r["usable"] = "overlay", False
        doc["screen"] = r
        side.write_text(yaml.safe_dump(doc, sort_keys=False))
        mark = "  <-- FLAGGED" if not r["usable"] else ""
        print(f"{doc['id']:<20} {r['overlay_ratio']:>8.4f} {r['axis_align']:>7.2f} "
              f"{r['motion']:>8.2f}  {r['verdict']}{mark}")
        if not r["usable"]:
            flagged.append((doc, side, media))

    if not flagged:
        print("\nall clips clean or unscreenable")
        return 0

    print(f"\n{len(flagged)} flagged: {', '.join(d['id'] for d, _, _ in flagged)}")
    if not args.reroll:
        print("re-run with --reroll to drop them and blacklist the source videos")
        return 0

    for doc, side, media in flagged:
        vid = (doc.get("provenance") or {}).get("video_id")
        if vid and vid not in rejected:
            rejected.append(vid)
        media.unlink(missing_ok=True)
        side.unlink(missing_ok=True)
        print(f"  dropped {doc['id']} (blacklisted {vid})")
    rejected_path.write_text(yaml.safe_dump(rejected, sort_keys=False))
    print(f"\nblacklist now {len(rejected)} ids -> {rejected_path}\n"
          f"re-run fetch_library.py to refill the empty slots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
