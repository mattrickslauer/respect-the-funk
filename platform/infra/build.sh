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
build="$here/build"

rm -rf "$build"
mkdir -p "$build"

python3 -m pip install \
  --target "$build" \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.13 \
  --only-binary=:all: --upgrade \
  fastapi jinja2 "psycopg[binary]" python-multipart mangum

# The app itself, templates included.
cp -r "$web/rtf_platform" "$build/rtf_platform"

# Trim what Lambda will never execute. Cold start is proportional to unpack size.
#
# .dist-info directories are deliberately NOT removed, tempting as it is: several
# of these packages resolve their own version through importlib.metadata at import
# time, and that reads dist-info. Deleting it saves a few hundred kilobytes and
# buys an ImportError that only appears in the deployed function.
find "$build" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$build" -type d -name "tests" -prune -exec rm -rf {} +

echo "built $build ($(du -sh "$build" | cut -f1))"
