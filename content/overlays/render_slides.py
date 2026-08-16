#!/usr/bin/env python3
"""Render the monitor slides to PNG.

    python3 render_slides.py

2560x1440 rather than 1920x1080 on purpose: these get corner-pinned onto a
screen in frame, which scales and rotates them, and a plate rendered at final
size goes soft the moment it is warped. The extra resolution is free here and
cannot be added later.

Opaque, unlike the overlay scenes — a monitor showing a transparent image is a
monitor that is off.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 2560, 1440


def main() -> int:
    slides = sorted((HERE / "slides").glob("*.html"))
    if not slides:
        print("no slides found", file=sys.stderr)
        return 1
    out = HERE / "out" / "slides"
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for s in slides:
            pg.goto(s.as_uri())
            pg.wait_for_timeout(180)          # let the webfont settle
            p = out / f"{s.stem}.png"
            pg.screenshot(path=str(p))
            print(p.relative_to(HERE))
        b.close()
        if errs:
            print("PAGE ERRORS:", *errs, sep="\n  ", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
