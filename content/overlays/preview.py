#!/usr/bin/env python3
"""Composite single frames of a scene over a flat colour, for looking at.

    python3 preview.py 03 0.5 3 6 9

Renders each time to out/preview/<scene>-t<time>.png on a mid-grey card, because
a transparent PNG viewed in anything that shows a checkerboard is impossible to
judge. The grey is deliberately mid — these plates have to hold over a bright
desk and a dark shirt alike, and mid-grey is where a plate that only works on
one of them shows it.

Use --bg to check the other extremes:  --bg dddbd6  /  --bg 141312
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("match")
    ap.add_argument("times", nargs="*", type=float, default=[])
    ap.add_argument("--bg", default="4a4a48")
    args = ap.parse_args()

    scenes = sorted(p for p in (HERE / "scenes").glob("*.html")
                    if p.name.startswith(args.match))
    if not scenes:
        print(f"no scene matching {args.match!r}", file=sys.stderr)
        return 1

    outdir = HERE / "out" / "preview"
    outdir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb"])
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for scene in scenes:
            pg.goto(scene.as_uri())
            pg.wait_for_function("window.SCENE_READY === true", timeout=15000)
            dur = pg.evaluate("window.SCENE_DURATION")
            times = args.times or [dur * f for f in (0.15, 0.4, 0.65, 0.92)]
            # Paint the card behind the SVG rather than compositing afterwards:
            # one less tool in the loop, and it proves the plate really is
            # transparent, since anything opaque would hide the card.
            pg.evaluate("(c) => document.body.style.background = '#' + c", args.bg)
            for t in times:
                pg.evaluate("(t) => window.seek(t)", t)
                p = outdir / f"{scene.stem}-t{t:g}.png"
                pg.screenshot(path=str(p))
                print(p.relative_to(HERE))
        b.close()
        if errs:
            print("PAGE ERRORS:", *errs, sep="\n  ", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
