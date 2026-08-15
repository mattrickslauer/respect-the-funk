#!/usr/bin/env bash
# Vendor the app and its dependencies into ./build for Terraform to zip.
#
# Dependencies are fetched as arm64 manylinux wheels rather than built locally,
# because the Lambda runs on Graviton and a wheel compiled against this machine's
# glibc will import fine here and fail there. --only-binary=:all: makes that a
# hard error at build time instead of a runtime ImportError in production.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Was `$here/../web` while this stack lived at `platform/infra`. It now sits under
# `infra/terraform/` with the other Terraform roots and the app under `apps/`, so the
# two are no longer siblings and this reaches back to the repo root to find it.
web="$here/../../../apps/spindle/web"
build="$here/build"

# Fail here rather than three commands later. Without this, a wrong path produces an
# empty `--target` install, a `cp` that copies nothing, and a zip Terraform will happily
# deploy as a Lambda that 500s on first request — the failure lands in production
# instead of at the build that caused it.
if [ ! -d "$web/spindle" ]; then
  echo "build.sh: no app at $web/spindle — the tree moved and this path did not" >&2
  exit 1
fi

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
cp -r "$web/spindle" "$build/spindle"

# There is no Node step here any more. `platform/console` — the React console this
# script used to compile into `spindle/console_dist` — was removed on 2026-08-15
# because the server-rendered console does the same job and is the one being built on.
# Its removal took npm out of the deployment path entirely, which is the reason this
# comment exists rather than nothing: a reader comparing an old zip against a new one
# will find a whole directory missing from the archive, and should find out here that
# it was taken out on purpose.

# Trim what Lambda will never execute. Cold start is proportional to unpack size.
#
# .dist-info directories are deliberately NOT removed, tempting as it is: several
# of these packages resolve their own version through importlib.metadata at import
# time, and that reads dist-info. Deleting it saves a few hundred kilobytes and
# buys an ImportError that only appears in the deployed function.
find "$build" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$build" -type d -name "tests" -prune -exec rm -rf {} +

echo "built $build ($(du -sh "$build" | cut -f1))"
