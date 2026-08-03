/**
 * Walk render.html frame by frame and write a PNG for each one.
 *
 * The page exposes `SEEK(t)`, which returns only once every video on screen has finished
 * seeking, so a screenshot taken after it resolves is never a half-decoded frame. Time
 * advances in exact 1/30s steps rather than by wall clock, which is what makes this
 * reproducible: pause the machine mid-run and the output is identical.
 *
 * Frames already on disk are skipped, so a crashed run resumes where it stopped and an
 * edit to one scene only costs the frames in that scene once the rest are deleted.
 *
 *   node capture.js --url http://localhost:8931/video/render.html --out out/frames \
 *                   --fps 30 --duration 120.05 [--from 40 --to 52] [--every 30]
 */

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const CHROME = process.env.CHROME_PATH ||
  process.env.HOME + "/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/" +
  "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";

function arg(name, dflt) {
  const i = process.argv.indexOf("--" + name);
  return i > 0 ? process.argv[i + 1] : dflt;
}

(async () => {
  const url = arg("url", "http://localhost:8931/video/render.html");
  const outDir = arg("out", "out/frames");
  const fps = parseFloat(arg("fps", "30"));
  const duration = parseFloat(arg("duration", "120"));
  const from = parseFloat(arg("from", "0"));
  const to = parseFloat(arg("to", String(duration)));
  const every = parseInt(arg("every", "1"), 10);   // 1 = every frame; N = contact sheet
  const force = process.argv.includes("--force");

  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({
    executablePath: CHROME,
    args: [
      "--autoplay-policy=no-user-gesture-required",
      "--force-device-scale-factor=1",
      "--disable-lcd-text",                 // grey AA survives h264 chroma better
      "--hide-scrollbars",
      "--force-color-profile=srgb",
      "--font-render-hinting=none",
    ],
  });
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
  });

  const misses = [];
  page.on("console", m => {
    const s = m.text();
    if (s.startsWith("CUE MISS")) misses.push(s);
    if (m.type() === "error") console.error("  page error:", s);
  });
  page.on("pageerror", e => { console.error("  PAGE EXCEPTION:", e.message); });

  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => window.READY === true, null, { timeout: 120000 });

  if (misses.length) {
    console.error("\n  cues that did not match the voice-over:");
    for (const m of new Set(misses)) console.error("   ", m);
    console.error("");
  }

  const first = Math.round(from * fps);
  const last = Math.min(Math.round(to * fps), Math.ceil(duration * fps));
  let written = 0, skipped = 0;
  const t0 = Date.now();

  for (let f = first; f < last; f += every) {
    const file = path.join(outDir, String(f).padStart(6, "0") + ".png");
    if (!force && fs.existsSync(file)) { skipped++; continue; }
    await page.evaluate(t => window.SEEK(t), f / fps);
    await page.screenshot({ path: file, type: "png" });
    written++;
    if (written % 60 === 0) {
      const el = (Date.now() - t0) / 1000;
      const done = written / Math.max(1, Math.ceil((last - first) / every) - skipped);
      process.stdout.write(
        `\r  ${written} frames · ${(written / el).toFixed(1)} fps · ` +
        `${(el / 60).toFixed(1)}m elapsed · ~${((el / done - el) / 60).toFixed(1)}m left   `);
    }
  }

  process.stdout.write("\n");
  console.log(`wrote ${written} frames, skipped ${skipped} already on disk`);
  await browser.close();
  if (misses.length) process.exitCode = 3;
})().catch(e => { console.error("capture failed:", e); process.exit(1); });
