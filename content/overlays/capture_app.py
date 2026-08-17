#!/usr/bin/env python3
"""Film the real app, so the video is a demo and not a diagram.

    python3 capture_app.py               # public site -> out/app/*.png + dial frames
    python3 capture_app.py --url http://127.0.0.1:8099 --cookie <token>

Everything here comes off the running product. The abstract scenes explain what
the system *is*; these show it actually doing it, which is the half a hackathon
judge is really looking for.

## Public first, on purpose

The landing page needs no account and carries the parts worth filming anyway:
counters that are live queries, a reach map you can click, a dial that reranks a
shortlist in front of you, the EXPLAIN in full, the send gate. Nothing here is a
mockup and nothing needs a login, so it can be re-captured by anyone at any time.

The console behind the OTP wall is richer still — roster, threads, inbox,
approvals, fleet — and needs `--cookie`. See the README for how to get one; this
script deliberately has no way to mint its own.

## The dial is the shot

`--dial` walks the range input across its travel and screenshots each step. That
is the single most convincing piece of footage available: a shortlist visibly
reordering under a control a viewer can see being moved, with no cut. Stills of a
table prove nothing; this proves the ranking is computed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "app"
W, H = 1920, 1080

# (name, anchor or None, settle ms). Anchors come from the live page.
SHOTS = [
    ("hero",      None,        1200),
    ("reach",     "#reach",     900),
    ("tune",      "#tune",      900),
    ("plan",      "#plan",      700),
    ("send",      "#send",      700),
    ("verdict",   "#verdict",   700),
]


def shoot(pg, name: str, anchor: str | None, settle: int) -> None:
    if anchor:
        try:
            pg.evaluate("(a) => { const e=document.querySelector(a); "
                        "if (e) e.scrollIntoView({block:'center'}); }", anchor)
        except Exception:
            pass
    else:
        pg.evaluate("window.scrollTo(0,0)")
    pg.wait_for_timeout(settle)
    p = OUT / f"{name}.png"
    pg.screenshot(path=str(p))
    print(f"  {p.relative_to(HERE)}")


def dial(pg, steps: int) -> int:
    """Walk the shortlist dial and capture each position."""
    sel = "input[type=range]"
    if pg.query_selector(sel) is None:
        print("  no range input on the page — skipping dial", file=sys.stderr)
        return 0
    pg.evaluate("(s) => { const e=document.querySelector(s); "
                "e.scrollIntoView({block:'center'}); }", sel)
    pg.wait_for_timeout(700)
    lo = float(pg.eval_on_selector(sel, "e => e.min || 0"))
    hi = float(pg.eval_on_selector(sel, "e => e.max || 100"))
    d = OUT / "dial"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(steps):
        v = lo + (hi - lo) * (i / max(1, steps - 1))
        # set + dispatch both events: some handlers listen for one, some the other
        pg.eval_on_selector(sel, """(e, v) => {
            e.value = v;
            e.dispatchEvent(new Event('input',  {bubbles:true}));
            e.dispatchEvent(new Event('change', {bubbles:true}));
        }""", v)
        pg.wait_for_timeout(260)          # let the rerank land
        pg.screenshot(path=str(d / f"{i:03d}.png"))
    print(f"  {steps} dial frames -> {d.relative_to(HERE)}")
    return steps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://spindle.mattrickslauer.com/")
    ap.add_argument("--cookie", help="rtf_session token, for console screens")
    ap.add_argument("--steps", type=int, default=24)
    # A 1920-wide page scaled into the 628px panel puts its body type at a third of
    # size, which is a picture of a UI rather than a readable one — the judging
    # guidance is explicit that a judge has to be able to READ the screen. Capturing
    # at a narrow viewport makes the page lay itself out for a small screen, so the
    # type is proportionally twice the size once it lands in the panel; the doubled
    # device scale factor keeps it sharp on the way down.
    ap.add_argument("--panel", action="store_true",
                    help="capture at panel proportions (960x640 @2x), not 1920x1080")
    args = ap.parse_args()

    w, h, dsf = (960, 640, 2) if args.panel else (W, H, 1)

    OUT.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = b.new_context(viewport={"width": w, "height": h},
                            device_scale_factor=dsf)
        if args.cookie:
            from urllib.parse import urlparse
            host = urlparse(args.url).hostname or "127.0.0.1"
            ctx.add_cookies([{"name": "rtf_session", "value": args.cookie,
                              "domain": host, "path": "/", "httpOnly": True}])
        pg = ctx.new_page()
        pg.goto(args.url, wait_until="networkidle", timeout=90000)
        # the counters are live queries; give them a beat to land
        pg.wait_for_timeout(1500)

        for name, anchor, settle in SHOTS:
            shoot(pg, name, anchor, settle)
        dial(pg, args.steps)

        if args.cookie:
            for route in ("roster", "counterparties", "threads", "inbox",
                          "approvals", "fleet", "budgets", "runs"):
                try:
                    pg.goto(args.url.rstrip("/") + "/" + route,
                            wait_until="networkidle", timeout=45000)
                    pg.wait_for_timeout(600)
                    p = OUT / f"console-{route}.png"
                    pg.screenshot(path=str(p))
                    print(f"  {p.relative_to(HERE)}")
                except Exception as e:
                    print(f"  {route}: {str(e)[:70]}", file=sys.stderr)

        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
