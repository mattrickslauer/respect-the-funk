#!/usr/bin/env python3
"""Render the architecture document to PDF.

    python3 build.py                 # -> spindle-architecture.pdf

One CSS pixel is one unit of PDF layout: the page box is declared 1600x1100 in
the stylesheet and printed at 1600x1100, so nothing reflows between what the
browser showed and what the file contains. Landscape, because every diagram in
here is wider than it is tall and this is read on a screen.

`printBackground` is on. These pages are dark by design — they match the deck
and the film — and a PDF whose background silently drops out prints white type
on white paper.

Exits non-zero on any page error or measured overrun. A diagram with a caption
clipped by its own viewBox still looks deliberate, which is exactly the failure
a reviewer skims past, so it has to fail the build instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "architecture.html"
OUT = HERE / "spindle-architecture.pdf"
W, H = 1600, 1100

#: Runs in the page. Every check here is one that a thumbnail would not show.
CHECKS_JS = r"""
() => {
  const bad = [];
  const TOL = 4;

  document.querySelectorAll('svg').forEach((svg, i) => {
    const vb = svg.viewBox.baseVal;
    const page = svg.closest('.page');
    const tag = page ? page.dataset.no : '?';
    svg.querySelectorAll('text').forEach(t => {
      let b;
      try { b = t.getBBox(); } catch (e) { return; }
      const over = [];
      if (b.x < vb.x - TOL) over.push('left');
      if (b.y < vb.y - TOL) over.push('top');
      if (b.x + b.width  > vb.x + vb.width  + TOL) over.push('right');
      if (b.y + b.height > vb.y + vb.height + TOL) over.push('bottom');
      if (over.length) bad.push(`p${tag}: "${(t.textContent||'').slice(0,38)}" off ${over.join('+')}`);
    });
  });

  // Boxes are drawn at explicit coordinates, so two of them can overlap without
  // anything reporting it. Compare every pair of <rect> in a page's diagram.
  // A caption can sit inside the viewBox and still run out of the box drawn
  // around it — the commonest failure here, and invisible at page scale.
  document.querySelectorAll('g.box').forEach(g => {
    const r = g.querySelector('rect');
    if (!r) return;
    const rx = +r.getAttribute('x'), ry = +r.getAttribute('y');
    const rw = +r.getAttribute('width'), rh = +r.getAttribute('height');
    const tag = g.closest('.page').dataset.no;
    g.querySelectorAll('text').forEach(t => {
      let b; try { b = t.getBBox(); } catch (e) { return; }
      if (b.x < rx - 2 || b.y < ry - 2 ||
          b.x + b.width > rx + rw + 2 || b.y + b.height > ry + rh + 2) {
        bad.push(`p${tag}: "${(t.textContent||'').slice(0,30)}" escapes its box`);
      }
    });
  });

  document.querySelectorAll('.page').forEach(page => {
    const rects = [...page.querySelectorAll('svg rect')].filter(r => +r.getAttribute('width') > 60);
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i], b = rects[j];
        const ax = +a.getAttribute('x'), ay = +a.getAttribute('y');
        const aw = +a.getAttribute('width'), ah = +a.getAttribute('height');
        const bx = +b.getAttribute('x'), by = +b.getAttribute('y');
        const bw = +b.getAttribute('width'), bh = +b.getAttribute('height');
        const ox = Math.min(ax + aw, bx + bw) - Math.max(ax, bx);
        const oy = Math.min(ay + ah, by + bh) - Math.max(ay, by);
        // A box fully inside another is deliberate (a chip, a nested band).
        const nested = (ax >= bx && ay >= by && ax + aw <= bx + bw && ay + ah <= by + bh) ||
                       (bx >= ax && by >= ay && bx + bw <= ax + aw && by + bh <= ay + ah);
        if (ox > 6 && oy > 6 && !nested) {
          bad.push(`p${page.dataset.no}: boxes overlap at ${Math.round(Math.max(ax,bx))},${Math.round(Math.max(ay,by))} by ${Math.round(ox)}x${Math.round(oy)}`);
        }
      }
    }
  });

  document.querySelectorAll('.page').forEach(p => {
    if (p.scrollHeight > p.clientHeight + TOL || p.scrollWidth > p.clientWidth + TOL) {
      bad.push(`p${p.dataset.no}: page overflows ${p.scrollWidth}x${p.scrollHeight}`);
    }
    const ft = p.querySelector('.foot');
    if (ft && ft.clientHeight > 48) bad.push(`p${p.dataset.no}: foot wraps (${ft.clientHeight}px)`);
    const f = p.querySelector('.fig');
    if (f && f.scrollHeight > f.clientHeight + 12) {
      bad.push(`p${p.dataset.no}: fig overflows by ${f.scrollHeight - f.clientHeight}px`);
    }
  });

  return { bad, pages: document.querySelectorAll('.page').length };
}
"""


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}", file=sys.stderr)
        return 1

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        pg.goto(SRC.as_uri())
        pg.wait_for_timeout(300)
        result = pg.evaluate(CHECKS_JS)

        pg.pdf(path=str(OUT), width=f"{W}px", height=f"{H}px",
               print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        b.close()

    problems = errs + result["bad"]
    print(f"{result['pages']} pages -> {OUT.relative_to(HERE.parent.parent)}"
          f"  ({OUT.stat().st_size // 1024} KB)")
    if problems:
        print("\nFAILED:", *problems[:24], sep="\n  ", file=sys.stderr)
        if len(problems) > 24:
            print(f"  … and {len(problems) - 24} more", file=sys.stderr)
        return 1
    print("no page errors, no overruns, no overlapping boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
