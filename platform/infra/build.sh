#!/usr/bin/env bash
# Vendor the console and its dependencies into ./build for Terraform to zip.
#
# Dependencies are fetched as arm64 manylinux wheels rather than built locally,
# because the Lambda runs on Graviton and a wheel compiled against this machine's
# glibc will import fine here and fail there. --only-binary=:all: makes that a
# hard error at build time instead of a runtime ImportError in production.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
web="$here/../web"
console="$here/../console"
build="$here/build"

rm -rf "$build"
mkdir -p "$build"

# Versions come from web/requirements.txt so the bundle and a local install cannot
# drift. A dependency that resolves differently in the two places is a bug you only
# find in production.
python3 -m pip install \
  --target "$build" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.13 \
  --only-binary=:all: --upgrade \
  -r "$web/requirements.txt"

# The app itself, templates included.
cp -r "$web/rtf_platform" "$build/rtf_platform"

# The React console, compiled here rather than copied from wherever it was left.
#
# Copying an existing `dist/` is the tempting version and it ships a lie: a checkout
# whose console source has moved on since the last `npm run build` would deploy the
# older bundle, and nothing in the plan, the apply, or the running function would say
# so. The failure surfaces as an operator reporting a fixed bug. Building here costs
# seconds and makes the bundle a function of the source.
#
# It lands *inside* the package, at `rtf_platform/console_dist`. Nothing outside
# `rtf_platform/` is in this archive, and `console_assets.DIST` looks here first for
# exactly that reason — see that module's docstring for the two layouts.
npm --prefix "$console" run build

# Sourcemaps are 85% of the output and are never read in Lambda: nothing there opens
# devtools. They stay in the checkout, where they are useful, and out of the zip,
# where they are unpack time on every cold start.
mkdir -p "$build/rtf_platform/console_dist"
cp -r "$console/dist/." "$build/rtf_platform/console_dist/"
find "$build/rtf_platform/console_dist" -name "*.map" -delete

# The pointer to the map has to go with it. Left behind, every operator who opens
# devtools fetches a 404 on a URL that looks exactly like a missing asset — which is
# the one diagnosis this deployment must not fake, since a genuinely missing chunk is
# the failure `console_assets.looks_like_a_file` exists to keep legible.
#
# Anchored to the start of the line and to the two comment forms Vite emits — `//#`
# for JavaScript, `/*#` for CSS — rather than matching the bare string anywhere. A
# loose match would delete any minified line that happened to contain it, and the
# corruption would ship silently as a blank console.
find "$build/rtf_platform/console_dist" \( -name "*.js" -o -name "*.css" \) \
  -exec sed -i -e '\|^//# sourceMappingURL=|d' -e '\|^/\*# sourceMappingURL=|d' {} +

# A deploy that silently serves the not-built page is the failure this whole
# arrangement exists to prevent, so assert the shell is actually in the archive.
test -f "$build/rtf_platform/console_dist/index.html" \
  || { echo "console build produced no index.html" >&2; exit 1; }

# Trim what Lambda will never execute. Cold start is proportional to unpack size.
#
# .dist-info directories are deliberately NOT removed, tempting as it is: several
# of these packages resolve their own version through importlib.metadata at import
# time, and that reads dist-info. Deleting it saves a few hundred kilobytes and
# buys an ImportError that only appears in the deployed function.
find "$build" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$build" -type d -name "tests" -prune -exec rm -rf {} +

echo "built $build ($(du -sh "$build" | cut -f1))"
