#!/usr/bin/env python3
"""Fit every scene to what was actually said, and burn the result onto the take.

    python3 compose.py                    # full build -> out/edit/spindle-cut.mp4
    python3 compose.py --preview 30 45    # just that slice, for looking at
    python3 compose.py --skip-frames      # re-encode from frames already rendered

Depends on out/edit/align.json, which `align.py` writes.

## The idea

Scenes are pure functions of time — `window.seek(t)` puts a scene wherever it
belongs at t. That property was originally about making renders reproducible, and
it turns out to buy something better: a scene can be rendered at ANY duration
without re-authoring it, by sampling its clock faster or slower.

So nothing here trims or loops. Each scene is *stretched or squeezed* onto the
window its own paragraphs occupy in the take. Scene 01 was authored at 26s and the
words it belongs to run 24.1s, so it is sampled at 0.93x and every beat inside it
still lands in the same relative place.

A trim would have thrown away the end of the scene, which is where the settled
state is. A loop would have restarted it mid-sentence. Rescaling keeps the whole
arc and just changes how fast it plays.

## One frame sequence, one overlay pass

The obvious build is thirteen overlay filters chained in ffmpeg. That decodes
thirteen ProRes 4444 streams at once for a 2:41 output and spends most of its time
on inputs that are transparent.

Instead every scene renders into ONE master frame sequence covering the whole
take, with fully transparent frames in the gaps between paragraphs. A transparent
1920x1080 PNG is about a kilobyte, so the gaps cost nothing, and the composite
becomes a single overlay of one image sequence onto one video.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENES = HERE / "scenes"
EDIT = HERE / "out" / "edit"
BASE = HERE.parent / "BaseLayer.mov"
FPS = 24                      # the take is 24fps; matching it avoids a resample
W, H = 1920, 1080

# Which paragraphs each scene is cut against. The paragraph indices are the ones
# align.py prints, which are the blank-line-separated blocks of the voiceover.
PLAN: list[tuple[str, list[int]]] = [
    ("01-disappear", [0, 1, 2]),
    ("02-spam",      [3]),
    ("03-subspace",  [4]),
    ("04-tenant",    [5]),
    ("05-pgvector",  [6]),
    ("06-filling",   [7]),
    ("07-fleet",     [8, 9]),
    ("08-lease",     [10]),
    ("09-token",     [11]),
    ("10-residency", [12, 13]),
    ("11-money",     [14]),
    ("12-replay",    [15, 16, 17, 18, 19]),
    ("13-close",     [20, 21]),
]

# A graphic that snaps on exactly as the first word lands reads as a mistake. It
# comes up just under the breath before, and holds just past the last word.
LEAD, TAIL = 0.35, 0.45
FADE = 0.30                   # seconds of alpha ramp at each edge


def probe_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def build_frames(align: list, total_frames: int) -> Path:
    frames = EDIT / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb", "--disable-lcd-text"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # one transparent plate, copied into every gap
        blank = EDIT / "blank.png"
        pg.goto("about:blank")
        pg.screenshot(path=str(blank), omit_background=True)

        written: set[int] = set()
        for stem, paras in PLAN:
            rows = [align[i] for i in paras if align[i]]
            if not rows:
                print(f"  {stem}: no aligned paragraphs, skipped", file=sys.stderr)
                continue
            start = max(0.0, min(r["start"] for r in rows) - LEAD)
            end = max(r["end"] for r in rows) + TAIL
            span = end - start

            scene = SCENES / f"{stem}.html"
            pg.goto(scene.as_uri())
            pg.wait_for_function("window.SCENE_READY === true", timeout=15000)
            authored = float(pg.evaluate("window.SCENE_DURATION"))

            f0 = int(round(start * FPS))
            n = int(round(span * FPS))
            stage = pg.query_selector("#stage")

            for k in range(n):
                u = k / max(1, n - 1)              # 0..1 through the window
                pg.evaluate("(t) => window.seek(t)", u * authored)
                # edge fade, applied to the whole stage so no scene needs to know
                sec = k / FPS
                a = min(1.0, sec / FADE, max(0.0, (span - sec)) / FADE)
                pg.evaluate("(a) => { document.getElementById('stage').style.opacity = a; }",
                            round(a, 4))
                idx = f0 + k
                if idx >= total_frames:
                    break
                pg.screenshot(path=str(frames / f"{idx:05d}.png"), omit_background=True)
                written.add(idx)

            print(f"  {stem:14} {start:7.2f} → {end:7.2f}  "
                  f"{span:5.2f}s  authored {authored:4.1f}s  x{span / authored:.3f}")

        b.close()
        if errs:
            print("PAGE ERRORS:", *errs, sep="\n  ", file=sys.stderr)
            raise SystemExit(1)

    # fill the gaps
    gaps = 0
    for i in range(total_frames):
        if i not in written:
            shutil.copyfile(blank, frames / f"{i:05d}.png")
            gaps += 1
    print(f"  {len(written)} scene frames, {gaps} transparent, {total_frames} total")
    return frames


def encode(frames: Path, out: Path, clip: tuple[float, float] | None) -> None:
    pre = ["-ss", str(clip[0]), "-to", str(clip[1])] if clip else []
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        *pre, "-i", str(BASE),
        "-framerate", str(FPS), "-start_number", "0", "-i", str(frames / "%05d.png"),
        "-filter_complex",
        # The overlay sequence starts at frame 0 of the take, so when a clip is
        # requested the sequence has to be shifted by the same amount rather than
        # restarted — otherwise the graphics play from the top over the middle.
        (f"[1:v]trim=start={clip[0]}:end={clip[1]},setpts=PTS-STARTPTS[ov];"
         if clip else "[1:v]null[ov];") +
        "[0:v][ov]overlay=0:0:format=auto:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", nargs=2, type=float, metavar=("FROM", "TO"))
    ap.add_argument("--skip-frames", action="store_true")
    args = ap.parse_args()

    if not BASE.exists():
        print(f"no base layer at {BASE}", file=sys.stderr)
        return 1
    apath = EDIT / "align.json"
    if not apath.exists():
        print("run align.py first", file=sys.stderr)
        return 1

    align = json.loads(apath.read_text())
    dur = probe_duration(BASE)
    total = int(round(dur * FPS))
    print(f"base {dur:.2f}s · {total} frames @ {FPS}fps")

    frames = EDIT / "frames"
    if not args.skip_frames:
        frames = build_frames(align, total)
    elif not frames.exists():
        print("--skip-frames given but out/edit/frames is missing", file=sys.stderr)
        return 1

    clip = tuple(args.preview) if args.preview else None
    out = EDIT / ("preview.mp4" if clip else "spindle-cut.mp4")
    encode(frames, out, clip)
    print(f"\n{out.relative_to(HERE)}  {out.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
