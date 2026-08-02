#!/usr/bin/env python3
"""Render the RemixKit application architecture to a PDF poster.

    python3 render.py            # writes system-architecture.html + .pdf

Eight sheets at 1800x1200 CSS px — 18.75in x 12.5in printed — covering the whole
application: who touches it, what every touch leaves behind, how a run is priced and
executed, where it deploys, and every refusal in the product.

This is generated rather than drawn for the same reason `infra/diagram.py` is: the
labels carry real routes, real object keys and real settings, and changing the
architecture means changing this file. `infra/architecture.pdf` is the AWS-shaped view
of the same system and deliberately stays a separate document.

Requires a Chromium-family browser for the PDF step (Brave, Chrome, Chromium, Edge).
The HTML stands on its own if none is installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kit import ASYNC, DIRECT, EXT, GATE, HOT, INK, MUTED, PAPER, PROV, RULE, SYNC  # noqa: E402
from pages_map import page_journey_a, page_journey_b, page_map  # noqa: E402
from pages_detail import page_deploy, page_pipeline, page_residue, page_trust  # noqa: E402

HERE = Path(__file__).parent
OUT_HTML = HERE / "system-architecture.html"
OUT_PDF = HERE / "system-architecture.pdf"

BROWSERS = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "chromium", "chromium-browser", "google-chrome",
]

CSS = f"""
@page {{ size: 1800px 1200px; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: {PAPER}; -webkit-print-color-adjust: exact;
  print-color-adjust: exact; }}
body {{ font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: {INK}; }}
.m, code, pre {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}
.m {{ font-size: 0.94em; letter-spacing: -0.1px; }}

.page {{ width: 1800px; height: 1200px; position: relative; overflow: hidden;
  background: {PAPER}; page-break-after: always; break-after: page; }}
.page:last-child {{ page-break-after: auto; }}

.ph {{ position: absolute; left: 0; top: 0; width: 1800px; height: 76px;
  padding: 14px 34px 0 34px; display: flex; align-items: flex-start; gap: 34px;
  border-bottom: 1px solid {RULE}; }}
.ph-l {{ flex: 0 0 620px; }}
.ph-l .eyebrow {{ display: block; font-size: 11px; font-weight: 700; letter-spacing: 2.4px;
  text-transform: uppercase; color: {HOT}; margin-bottom: 3px; }}
.ph-l h2 {{ margin: 0; font-size: 25px; font-weight: 700; letter-spacing: -0.5px; line-height: 1.1; }}
.ph-r {{ flex: 1; font-size: 12.5px; line-height: 1.45; color: {MUTED}; padding-top: 14px;
  max-width: 900px; }}
.pn {{ position: absolute; right: 30px; top: 12px; font-size: 30px; font-weight: 700;
  color: {INK}; letter-spacing: -1px; }}
.pn span {{ font-size: 15px; color: {RULE}; font-weight: 700; }}

.canvas {{ position: absolute; left: 0; top: 78px; width: 1800px; height: 1090px; }}
.wires {{ position: absolute; left: 0; top: 0; }}

.pf {{ position: absolute; left: 34px; right: 34px; bottom: 8px; height: 22px;
  border-top: 1px solid {RULE}; padding-top: 5px; font-size: 10.5px; color: {MUTED};
  display: flex; justify-content: space-between; }}

.node {{ position: absolute; border: 1.5px solid; border-radius: 9px; padding: 8px 11px;
  overflow: hidden; }}
.node .badge + .t {{ padding-right: 28px; }}
.node .t {{ font-size: 13px; font-weight: 700; line-height: 1.22; letter-spacing: -0.15px; }}
.node .s {{ font-size: 10.6px; font-weight: 600; line-height: 1.3; margin-top: 2px; }}
.node .i {{ margin: 5px 0 0 0; padding: 0; list-style: none; }}
.node .i li {{ font-size: 10.6px; line-height: 1.34; color: #3B424D; margin-bottom: 3px;
  padding-left: 8px; position: relative; }}
.node .i li:before {{ content: "·"; position: absolute; left: 0; color: {RULE}; font-weight: 700; }}
.node .f {{ font-size: 10px; line-height: 1.3; color: {MUTED}; margin-top: 5px; font-style: italic; }}
.node .badge {{ position: absolute; right: 8px; top: 7px; color: #fff; font-size: 9.5px;
  font-weight: 700; padding: 1.5px 6px; border-radius: 20px; letter-spacing: 0.4px; }}
.k-actor .i li {{ color: #C6CBD4; }}
.k-actor .i li:before {{ color: #555C68; }}
.k-actor .f {{ color: #99A0AC; }}
.k-actor .badge {{ background: #fff !important; color: {INK} !important; }}
.k-ghost .i li {{ color: {MUTED}; }}

.note {{ position: absolute; line-height: 1.42; }}
.tag {{ position: absolute; border: 1px solid; border-radius: 20px; padding: 2px 9px;
  font-weight: 700; letter-spacing: 0.3px; }}
.raw {{ position: absolute; }}

table.tbl {{ border-collapse: collapse; width: 100%; font-size: 10.6px; line-height: 1.36; }}
table.tbl th {{ text-align: left; font-size: 9.5px; letter-spacing: 1.3px; text-transform: uppercase;
  color: {MUTED}; font-weight: 700; padding: 0 9px 5px 0; border-bottom: 1.5px solid {INK}; }}
table.tbl td {{ padding: 6px 9px 6px 0; border-bottom: 1px solid {RULE}; vertical-align: top;
  color: #343B45; }}
table.tbl tr:last-child td {{ border-bottom: none; }}
table.tbl td:first-child {{ color: {INK}; }}

table.tree2 {{ border-collapse: collapse; width: 100%; }}
table.tree2 td {{ padding: 1.5px 0; vertical-align: baseline; }}
table.tree2 td.p {{ font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 11.4px;
  white-space: pre; color: #343B45; padding-right: 22px; }}
table.tree2 td.c {{ font-size: 10.6px; font-style: italic; color: {MUTED}; width: 46%; }}
table.tree2 .kp {{ color: {HOT}; font-weight: 700; }}
table.tree2 .v {{ color: {EXT}; }}
.cm {{ color: {MUTED}; font-style: italic; font-family: "Helvetica Neue", Helvetica, Arial; }}

/* cover */
.cover {{ padding: 0; }}
.cv-rule {{ position: absolute; left: 90px; top: 88px; width: 1620px; height: 6px; background: {INK}; }}
.cv-eye {{ position: absolute; left: 90px; top: 118px; font-size: 14px; font-weight: 700;
  letter-spacing: 5px; text-transform: uppercase; color: {HOT}; }}
.cv-h1 {{ position: absolute; left: 86px; top: 158px; font-size: 74px; font-weight: 700;
  letter-spacing: -3px; line-height: 1.0; margin: 0; }}
.cv-sub {{ position: absolute; left: 92px; top: 432px; width: 830px; font-size: 17px;
  line-height: 1.52; color: #3B424D; }}
.cv-meta {{ position: absolute; left: 92px; bottom: 76px; font-size: 12.5px; color: {MUTED};
  line-height: 1.7; }}
.cv-toc {{ position: absolute; right: 90px; top: 240px; width: 700px; }}
.cv-toc .row {{ display: flex; gap: 18px; padding: 13px 0; border-top: 1px solid {RULE};
  align-items: baseline; }}
.cv-toc .n {{ flex: 0 0 42px; font-size: 20px; font-weight: 700; color: {RULE}; }}
.cv-toc .ti {{ flex: 1; }}
.cv-toc .ti b {{ display: block; font-size: 16.5px; letter-spacing: -0.2px; }}
.cv-toc .ti span {{ font-size: 12px; color: {MUTED}; line-height: 1.4; display: block;
  margin-top: 2px; }}
.cv-key {{ position: absolute; left: 92px; top: 730px; width: 830px; }}
.cv-key .k {{ display: flex; align-items: center; gap: 14px; padding: 7px 0; }}
.cv-key .sw {{ flex: 0 0 54px; height: 4px; border-radius: 3px; }}
.cv-key .lb {{ font-size: 13px; }}
.cv-key .lb b {{ font-size: 13.5px; }}
.cv-key .lb span {{ color: {MUTED}; }}
"""

TOC = [
    ("01", "The whole system on one sheet",
     "Actors, front doors, thirteen services, eight ports, seven adapter axes, one bucket — and the asynchronous lane everything slow is pushed into."),
    ("02", "The journey · onboarding, and the master",
     "Nine touchpoints from the sign-in code to the hook window, each with what it writes, what it records about how, and what undoing it reaches."),
    ("03", "The journey · buying a run, and what leaves",
     "Nine more, from the priced dry run to deletion. Two of them write nothing at all; one of them is where every dollar goes."),
    ("04", "Residual actions — the ledger",
     "The whole key space, every durable record, what each claim rests on, and the exact limits of every undo."),
    ("05", "The expensive path",
     "One resolution function with two callers, the two-stage identity lock, the still index, and every refusal a plan screen can raise."),
    ("06", "Deployment",
     "Lambda, SQS, Batch on Fargate Spot, SSM and B2 — against the same code running on a laptop with no credentials."),
    ("07", "The trust argument",
     "The provenance loop, the fifteen refusals in the product, and what is deliberately absent."),
]

KEY = [
    (SYNC, False, "request / response", "the ordinary synchronous path"),
    (HOT, False, "durable bytes", "everything that outlives the process"),
    (ASYNC, False, "queued work", "never inside an HTTP request"),
    (DIRECT, True, "browser ↔ bucket", "presigned; the bytes never enter compute"),
    (EXT, True, "a vendor call", "billed — and observably billed on failure too"),
    (PROV, False, "provenance", "the claim that travels inside the file"),
    (GATE, False, "a refusal", "on purpose, in the service layer, with a test"),
]


def cover() -> str:
    toc = "".join(
        f'<div class="row"><div class="n">{n}</div><div class="ti"><b>{t}</b>'
        f'<span>{d}</span></div></div>' for n, t, d in TOC)
    key = "".join(
        f'<div class="k"><div class="sw" style="background:{c};'
        f'{"background:repeating-linear-gradient(90deg,%s 0 7px,transparent 7px 12px)" % c if dash else ""}">'
        f'</div><div class="lb"><b style="color:{c}">{name}</b> &nbsp;<span>{note}</span></div></div>'
        for c, dash, name, note in KEY)
    return f"""
<section class="page cover">
  <div class="cv-rule"></div>
  <div class="cv-eye">RemixKit &nbsp;·&nbsp; the artist console</div>
  <h1 class="cv-h1">System architecture,<br>and every trace<br>a user leaves.</h1>
  <div class="cv-sub">A record label registers an artist, builds that artist's identity once, attaches
    songs, measures each master, generates content designed to be imitated, approves it, and hands it
    off with the disclosure travelling inside the file.<br><br>
    This document maps that end to end: every surface a person touches, every document and object the
    touch writes, every claim the system makes about <i>how</i> it knows something, and the exact
    boundary of what taking it back can reach.</div>
  <div class="cv-key">{key}</div>
  <div class="cv-toc">{toc}</div>
  <div class="cv-meta">
    Generated by <span class="m">docs/architecture/render.py</span> — labels carry the real routes,
    object keys and settings, so the picture cannot drift from the code.<br>
    Companion to <span class="m">infra/architecture.pdf</span>, which is the AWS-shaped view of the
    same system.<br>
    Sheets are 18.75&#8202;×&#8202;12.5&#8202;in — built to be read on screen at 100% and printed at A2.
  </div>
</section>"""


def build_html() -> str:
    pages = [cover(), page_map(), page_journey_a(), page_journey_b(),
             page_residue(), page_pipeline(), page_deploy(), page_trust()]
    return ("<!-- generated by docs/architecture/render.py — do not edit -->\n"
            f"<meta charset='utf-8'><title>RemixKit — system architecture</title>"
            f"<style>{CSS}</style>" + "".join(pages))


def find_browser():
    for b in BROWSERS:
        if b.startswith("/") and Path(b).exists():
            return b
        found = shutil.which(b)
        if found:
            return found
    return None


def main() -> int:
    OUT_HTML.write_text(build_html(), encoding="utf-8")
    print(f"wrote {OUT_HTML.relative_to(HERE.parent.parent)}")

    browser = find_browser()
    if not browser:
        print("no Chromium-family browser found — the HTML is complete, the PDF was skipped",
              file=sys.stderr)
        return 1
    cmd = [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not OUT_PDF.exists():
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1
    print(f"wrote {OUT_PDF.relative_to(HERE.parent.parent)} "
          f"({OUT_PDF.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
