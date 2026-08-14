// =============================================================================
// sync-tokens.mjs — the design system has one source, and it is not this app
// =============================================================================
//
// `platform/web/rtf_platform/templates/_design.html` is the source of truth for
// the palette, the type scale and the shared components. It has to be: it is what
// the landing page and the operator manual include, and those two ship today
// whether or not this app ever builds.
//
// So this app does not get its own copy. It gets a generated one, written here
// and gitignored, regenerated before every `dev` and `build`. The alternative —
// hand-maintaining a second CSS file with the same hex values in it — is exactly
// the drift `_design.html` was extracted to stop, and it would be a poor joke to
// reintroduce it in the app that motivated the extraction.
//
// `--check` exits non-zero if the generated file is stale, for CI.
//
// Why a script and not a bundler plugin or a CSS import: this needs to run before
// `tsc` as well as before Vite, it needs to work in CI without installing the app,
// and it is twenty lines. A plugin would be cleverer and would only run inside
// Vite, which is the one place the file is already going to be correct.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = resolve(here, "../../web/rtf_platform/templates/_design.html");
const TARGET = resolve(here, "../src/styles/design.generated.css");

const BANNER = `/* GENERATED — do not edit.
 * Source: platform/web/rtf_platform/templates/_design.html
 * Regenerate: npm run sync-tokens
 *
 * Edit the source. Editing this file is how the palette forks, and the palette
 * forking is the specific failure the source file exists to prevent.
 */\n\n`;

function extract(html) {
  const open = html.indexOf("<style>");
  const close = html.lastIndexOf("</style>");
  if (open === -1 || close === -1 || close < open) {
    // Fail loudly. A silent empty stylesheet renders as unstyled-but-not-broken,
    // which is the worst possible failure: it looks like a design decision.
    throw new Error(
      `sync-tokens: no <style> block found in ${SOURCE}. The design system did ` +
        `not move on its own — find out what changed before regenerating.`,
    );
  }
  const css = html.slice(open + "<style>".length, close).trim();
  if (!css.includes("--phosphor")) {
    throw new Error(
      "sync-tokens: extracted CSS has no --phosphor token. That file is either " +
        "not the design system or has been gutted; refusing to write it.",
    );
  }
  return BANNER + css + "\n";
}

const wanted = extract(readFileSync(SOURCE, "utf8"));
const check = process.argv.includes("--check");
const current = existsSync(TARGET) ? readFileSync(TARGET, "utf8") : null;

if (check) {
  if (current !== wanted) {
    console.error(
      "sync-tokens: design.generated.css is stale. Run `npm run sync-tokens`.",
    );
    process.exit(1);
  }
  console.log("sync-tokens: up to date.");
} else if (current === wanted) {
  console.log("sync-tokens: up to date.");
} else {
  mkdirSync(dirname(TARGET), { recursive: true });
  writeFileSync(TARGET, wanted);
  console.log(`sync-tokens: wrote ${join("src/styles", "design.generated.css")}`);
}
