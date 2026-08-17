#!/usr/bin/env python3
"""Render the submission deck to PNG.

    python3 render.py            # all slides -> out/
    python3 render.py 01 04      # just those, by filename prefix

3:2 at 2400x1600 because that is the ratio Devpost asks for and it downscales
everything anyway. Rendered at device_scale_factor=1: these are screenshots of
vector art rather than plates that get warped later, so oversampling buys
nothing but file size, and the gallery has a 5 MB per-image ceiling.

Exits non-zero on any page error. A slide whose script threw renders as a
half-drawn diagram that still looks deliberate, which is exactly the failure a
human reviewer misses, so it has to fail the run instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 2400, 1600

#: Runs in the page. Returns one string per text node that leaves its own viewBox,
#: and one per block element that overflows the plate. Tolerance is 4 user units:
#: a glyph's bbox includes side bearing, so exact-fit type reports as a hair over.
OVERRUN_JS = r"""
() => {
  const bad = [];
  const TOL = 4;
  document.querySelectorAll('svg').forEach(svg => {
    const vb = svg.viewBox.baseVal;
    svg.querySelectorAll('text').forEach(t => {
      let b;
      try { b = t.getBBox(); } catch (e) { return; }
      const over = [];
      if (b.x < vb.x - TOL) over.push('left');
      if (b.y < vb.y - TOL) over.push('top');
      if (b.x + b.width  > vb.x + vb.width  + TOL) over.push('right');
      if (b.y + b.height > vb.y + vb.height + TOL) over.push('bottom');
      if (over.length) {
        const s = (t.textContent || '').slice(0, 34);
        bad.push(`"${s}" off ${over.join('+')}`);
      }
    });
  });
  // The bottom rail is one line by design. It wraps silently when a caption
  // runs long, which reads as sloppy rather than as broken, so it is measured.
  const rail = document.querySelector('.rail');
  if (rail && rail.clientHeight > 78) bad.push(`rail wraps (${rail.clientHeight}px)`);

  // A flex child with min-height:0 lets its content spill without growing the
  // plate, so the plate-level check below misses it entirely. That is exactly
  // how slide 15 ended up printing its closing line on top of the rail.
  // Only .fig — it is the flex:1 child, the one with a height it can exceed.
  // Checking .head too produced an 8px false positive on every slide: a text
  // block's scrollHeight includes the last line's descender box, so an
  // auto-height element reports as overflowing itself.
  document.querySelectorAll('.fig').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 12) {
      bad.push(`fig overflows by ${el.scrollHeight - el.clientHeight}px`);
    }
  });

  const plate = document.querySelector('.plate');
  if (plate && (plate.scrollHeight > plate.clientHeight + TOL ||
                plate.scrollWidth  > plate.clientWidth  + TOL)) {
    bad.push(`plate overflows ${plate.scrollWidth}x${plate.scrollHeight}`);
  }
  return bad;
}
"""


def main(argv: list[str]) -> int:
    slides = sorted(HERE.glob("[0-9][0-9]-*.html"))
    if argv:
        slides = [s for s in slides if any(s.name.startswith(a) for a in argv)]
    if not slides:
        print("no slides matched", file=sys.stderr)
        return 1

    out = HERE / "out"
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        for s in slides:
            del errs[:]
            pg.goto(s.as_uri())
            pg.wait_for_timeout(220)          # webfont + the inline draw scripts

            # Label overrun is the failure mode of this deck, and it is invisible
            # in a thumbnail: a caption two characters too long is simply cut off
            # by the viewBox edge and the slide still looks deliberate. Measuring
            # is cheap and eyeballing fifteen plates is not, so every text node is
            # checked against the box it is drawn in.
            spills = pg.evaluate(OVERRUN_JS)

            path = out / f"{s.stem}.png"
            pg.screenshot(path=str(path))
            kb = path.stat().st_size // 1024

            note = ""
            if errs:
                failures.append(f"{s.name}: {errs[0]}")
                note = f"   PAGE ERROR: {errs[0]}"
            elif spills:
                failures.append(f"{s.name}: {len(spills)} text overruns")
                note = "   OVERRUN: " + " | ".join(spills[:3])
            print(f"  {s.stem}.png  {kb} KB{note}")
        b.close()

    if failures:
        print("\nFAILED:", *failures, sep="\n  ", file=sys.stderr)
        return 1
    print(f"\n{len(slides)} slides -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
