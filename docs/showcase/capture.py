"""Capture the fifteen showcase screens from a running console.

Point it at a local instance on the zero-credential path — see README.md. Ids come from
the environment so this works against whatever records the instance actually holds:

    RK_SHOWCASE_BASE    http://localhost:8077
    RK_SHOWCASE_ARTIST  art_...
    RK_SHOWCASE_SONG    sng_...
    RK_SHOWCASE_KIT     kit_...   (a kit with delivered assets)

Requires the Chrome-for-Testing chromium Playwright ships (`playwright install chromium`).
The open-source build has no H.264 decoder and every delivered clip would come out black.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = os.environ.get("RK_SHOWCASE_BASE", "http://localhost:8077")
ARTIST = os.environ.get("RK_SHOWCASE_ARTIST", "art_5caad949d23c")
SONG = os.environ.get("RK_SHOWCASE_SONG", "sng_f438a34afbb9")
KIT = os.environ.get("RK_SHOWCASE_KIT", "kit_bd336e274e9b")

OUT = Path(__file__).parent
VIEWPORT = {"width": 1440, "height": 900}

# Seek every video to a frame with something in it. `preload=metadata` in the template is
# right for the app and wrong for a screenshot: the poster frame never decodes on its own.
PRIME_VIDEO = """
async () => {
  const vids = [...document.querySelectorAll('video')];
  await Promise.all(vids.map(v => new Promise(res => {
    v.muted = true;
    v.preload = 'auto';
    const seek = () => {
      const d = isFinite(v.duration) && v.duration > 0 ? v.duration : 2;
      v.currentTime = Math.min(1.4, d * 0.3);
      v.addEventListener('seeked', res, {once: true});
      setTimeout(res, 2500);
    };
    if (v.readyState >= 1) seek();
    else { v.addEventListener('loadedmetadata', seek, {once: true}); setTimeout(res, 3000); }
    v.load();
  })));
}
"""


def prime(page: Page, settle: int = 900) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("() => document.fonts.ready")
    if page.query_selector("video"):
        page.evaluate(PRIME_VIDEO)
    page.wait_for_timeout(settle)


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), animations="disabled")
    print(f"  {name}.png")


def delivered_asset() -> Path:
    """One delivered file, downloaded with its manifest embedded, to verify at the end."""
    import re

    html = urllib.request.urlopen(f"{BASE}/console/kits/{KIT}").read().decode()
    href = re.search(r'href="(/api/v1/kits/[^"]+/download)"', html).group(1)
    path = Path(os.environ.get("TMPDIR", "/tmp")) / "rk-showcase-delivered.mp4"
    urllib.request.urlretrieve(BASE + href, path)
    return path


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chromium")
    page = browser.new_context(
        viewport=VIEWPORT, device_scale_factor=2, reduced_motion="reduce"
    ).new_page()

    # 01 — the public site. Its sections reveal on scroll, so scroll the whole page first.
    page.goto(f"{BASE}/")
    prime(page)
    page.evaluate("""async () => {
      const step = window.innerHeight * 0.6;
      for (let y = 0; y < document.body.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise(r => setTimeout(r, 120));
      }
      window.scrollTo(0, 0);
      await new Promise(r => setTimeout(r, 300));
    }""")
    prime(page, settle=1200)
    shot(page, "01-landing-hero")

    # 02–04 — the roster, an artist, and the kits built for them.
    page.goto(f"{BASE}/console")
    prime(page)
    shot(page, "02-roster")

    page.goto(f"{BASE}/console/artists/{ARTIST}")
    prime(page)
    shot(page, "03-artist-consent")

    page.click("label[for='pane-2']")  # Kits
    prime(page, settle=1500)
    shot(page, "04-artist-kits")

    # 05–06 — the identity, then the reference frames further down the same pane.
    page.goto(f"{BASE}/console/artists/{ARTIST}/identity")
    prime(page, settle=1200)
    shot(page, "05-identity-builder")

    page.evaluate("""() => {
      const gallery = [...document.querySelectorAll('img')]
        .filter(i => i.getBoundingClientRect().top > 600)[0];
      if (gallery) gallery.scrollIntoView({block: 'start'});
      window.scrollBy(0, -120);
    }""")
    page.wait_for_timeout(1200)
    shot(page, "06-identity-frames")

    # 07–09 — the measurement: sections, arrangement, and which hook to cut to.
    page.goto(f"{BASE}/console/songs/{SONG}")
    prime(page)
    shot(page, "07-song-sections")

    page.click("label[for='pane-3']")  # Arrangement
    page.wait_for_timeout(700)
    shot(page, "08-song-arrangement")

    page.click("label[for='pane-2']")  # Recommendations
    page.wait_for_timeout(700)
    shot(page, "09-song-recommendations")

    # 10 — what every song is still blocked on.
    page.goto(f"{BASE}/console/catalogue")
    prime(page)
    shot(page, "10-catalogue-gaps")

    # 11–12 — the plan the button submits, and the shot list underneath it.
    page.goto(f"{BASE}/console/artists/{ARTIST}/songs/{SONG}/generate")
    prime(page)
    shot(page, "11-generate-plan")

    page.evaluate("""() => {
      const el = [...document.querySelectorAll('*')].find(n =>
        n.children.length === 0 && /STAGE 1/i.test(n.textContent || ''));
      if (el) el.scrollIntoView({block: 'center'});
    }""")
    page.wait_for_timeout(600)
    shot(page, "12-generate-shot-list")

    # 13–14 — what came out, and what it cost.
    page.goto(f"{BASE}/console/kits/{KIT}")
    prime(page, settle=1800)
    shot(page, "13-kit-assets")

    page.click("label[for='pane-3']")  # Cost ledger
    page.wait_for_timeout(700)
    shot(page, "14-kit-cost-ledger")

    # 15 — a delivered file handed back to the verifier. Scope the click to the form:
    # the sidebar has a "Verify" link that would navigate away and clear the upload.
    page.goto(f"{BASE}/verify")
    prime(page)
    page.set_input_files("input[type=file]", str(delivered_asset()))
    page.wait_for_timeout(400)
    page.click("form button:has-text('Verify'), form input[type=submit]")
    page.wait_for_timeout(6000)
    prime(page)
    shot(page, "15-verify-pass")

    browser.close()
