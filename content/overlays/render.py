#!/usr/bin/env python3
"""Render overlay scenes to alpha-channel video.

    python3 render.py                 # every scene, ProRes 4444 + WebM
    python3 render.py 03              # just the scenes whose name starts 03
    python3 render.py --fps 60 --png  # also keep the frame sequence

Why frame-by-frame instead of screen-recording a page: the scenes are pure
functions of time (see lib/scene.js), so we drive the clock ourselves and get a
deterministic plate. Re-rendering scene 12 next week produces byte-identical
frames to the one already in the timeline, which is the property that makes it
safe to tweak a scene after the edit has started.

Two outputs, because editors want different things:

  .mov  ProRes 4444 — straight alpha, what you drop on a track in Resolve or
        Premiere. Big files; that is the trade for not having to think about
        premultiplication.
  .webm VP9 + yuva420p — small, for previewing in a browser and for anyone
        compositing on the web. Chroma is subsampled, so it is a preview format
        and not the one to cut with.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENES = HERE / "scenes"
OUT = HERE / "out"
W, H = 1920, 1080


def render_frames(page, scene: Path, fps: int, tmp: Path) -> int:
    page.goto(scene.as_uri())
    page.wait_for_function("window.SCENE_READY === true", timeout=15000)
    dur = page.evaluate("window.SCENE_DURATION")
    n = int(round(dur * fps))
    for i in range(n):
        page.evaluate("(t) => window.seek(t)", i / fps)
        # omit_background is the whole point — it drops the white page canvas and
        # leaves the alpha the SVG actually painted.
        page.screenshot(path=str(tmp / f"{i:05d}.png"), omit_background=True)
    return n


def encode(tmp: Path, stem: str, fps: int) -> list[str]:
    made = []
    src = ["-framerate", str(fps), "-i", str(tmp / "%05d.png")]

    mov = OUT / f"{stem}.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *src,
         # 4444 is the profile that carries alpha; -pix_fmt yuva444p10le is what
         # makes ffmpeg actually keep it rather than silently flattening.
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
         "-alpha_bits", "16", str(mov)],
        check=True)
    made.append(mov.name)

    webm = OUT / f"{stem}.webm"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *src,
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
         "-auto-alt-ref", "0", str(webm)],
        check=True)
    made.append(webm.name)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("match", nargs="?", default="")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--png", action="store_true", help="keep the frame sequence")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg not on PATH", file=sys.stderr)
        return 1

    scenes = sorted(p for p in SCENES.glob("*.html") if p.name.startswith(args.match))
    if not scenes:
        print(f"no scenes matching {args.match!r} in {SCENES}", file=sys.stderr)
        return 1

    OUT.mkdir(exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb",
                                           "--disable-lcd-text"])
        page = browser.new_page(viewport={"width": W, "height": H},
                                device_scale_factor=1)
        for scene in scenes:
            stem = scene.stem
            tmp = OUT / f".frames-{stem}"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            n = render_frames(page, scene, args.fps, tmp)
            made = encode(tmp, stem, args.fps)
            if args.png:
                seq = OUT / f"{stem}-png"
                if seq.exists():
                    shutil.rmtree(seq)
                tmp.rename(seq)
                made.append(f"{seq.name}/ ({n} frames)")
            else:
                shutil.rmtree(tmp)
            print(f"{stem:24} {n:4} frames @ {args.fps}fps  ->  {', '.join(made)}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
