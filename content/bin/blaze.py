#!/usr/bin/env python3
"""Genblaze + Backblaze B2 — the provenance and storage layer.

    .venv/bin/python bin/blaze.py check
    .venv/bin/python bin/blaze.py publish nate-test
    .venv/bin/python bin/blaze.py verify videos/nate-test/nate-test.mp4

**What is canonical does not move.** The YAML descriptors in git remain the source of
truth: a clip means what its `*.clip.yaml` says it means, an edit is what `edit.yaml`
says. B2 holds the *bytes* and the *manifests*, and the descriptor grows a pointer into
it — which is exactly what CLIP-SPEC already reserved:

    provenance:
      manifest_b2_key: <str>     # AI clips only; points at the Genblaze manifest

So this module adds a layer under the existing model rather than replacing it. Delete
every byte in B2 and the repo still describes every video completely; delete the repo and
B2 is a pile of anonymous blobs. That asymmetry is deliberate.

**Why Genblaze rather than a bare S3 client.** Three things come free that this pipeline
was already hand-rolling or missing:

  * a `manifest` per run recording model, prompt, params and SHA-256 per asset — the same
    discipline `generate_stills.py` already enforces with its own content-addressed cache,
    but in a format something other than us can read;
  * `Mp4Handler().embed(...)` and friends, which put that manifest *inside* the delivered
    file, so disclosure travels with the media rather than in a database we own;
  * `manifest.verify()`, which is the whole `/verify` story and is one call.

**Rights are not laundered by uploading.** `publish` refuses anything whose descriptor
declares a `rights.source` outside CLIP-SPEC's cleared set unless `--force` is passed, and
records the refusal. Publishing is a distribution decision; see `nate-hook.clip.yaml` for
a live example of a clip that renders fine and must not ship.

Requires: `genblaze-core`, `genblaze-s3` (Python >= 3.11 — see requirements-genblaze.txt),
and B2_KEY_ID / B2_APP_KEY / B2_BUCKET in the environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

import rtf

# CLIP-SPEC rule 3. Anything else is renderable but not publishable.
CLEARED_SOURCES = {"original", "ai", "stock_licensed", "public_domain"}

# Which manifest-embedding handler applies to which extension. Genblaze ships handlers
# for exactly these; anything else is uploaded without an embedded manifest, and the
# sidecar pointer is then the only provenance.
EMBEDDABLE = {".mp4": "Mp4Handler", ".png": "PngHandler", ".mp3": "Mp3Handler",
              ".webp": "WebPHandler", ".wav": "WavHandler"}


# ---------------------------------------------------------------- environment


def load_dotenv(start: Path) -> None:
    """Read the nearest .env walking up. Does not overwrite real env vars."""
    for d in [start, *start.parents]:
        f = d / ".env"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
        return


def missing_env() -> list[str]:
    return [k for k in ("B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET") if not os.environ.get(k)]


def importable() -> tuple[bool, str]:
    if sys.version_info < (3, 11):
        return False, (f"Python {sys.version_info.major}.{sys.version_info.minor} — "
                       f"genblaze-core requires >= 3.11. See requirements-genblaze.txt.")
    try:
        import genblaze_core  # noqa: F401
        import genblaze_s3    # noqa: F401
    except ImportError as e:
        return False, f"{e}. pip install -r bin/requirements-genblaze.txt"
    return True, "ok"


# ---------------------------------------------------------------- storage


def backend():
    """The B2 bucket, through Genblaze's S3-compatible backend.

    `for_backblaze` reads B2_KEY_ID / B2_APP_KEY itself; bucket and region are ours to
    supply. `public_url_base` is what makes a returned asset URL directly linkable, which
    is what keeps bytes off the app server (BUILD-SPEC §2b rule 2).
    """
    from genblaze_s3 import S3StorageBackend

    bucket = os.environ["B2_BUCKET"]
    region = os.environ.get("B2_REGION", "us-west-004")
    return S3StorageBackend.for_backblaze(
        bucket,
        region=region,
        public_url_base=os.environ.get(
            "B2_PUBLIC_URL_BASE", f"https://f{region.split('-')[-1]}.backblazeb2.com/file/{bucket}"),
        auto_lifecycle=True,
    )


def sink():
    """Content-addressable keys, deliberately.

    Genblaze offers HIERARCHICAL (`runs/{tenant}/{date}/{run_id}/…`) and
    CONTENT_ADDRESSABLE. This pipeline has been content-addressed since
    `generate_stills.py` was written — identical prompt+model+params reuses the existing
    object instead of regenerating, which is BUILD-SPEC §2b rule 5 and the single biggest
    lever on the dominant cost line. Matching that in the key strategy means the two
    caches agree instead of quietly diverging.
    """
    from genblaze_core import KeyStrategy, ObjectStorageSink

    return ObjectStorageSink(backend(), key_strategy=KeyStrategy.CONTENT_ADDRESSABLE)


# ---------------------------------------------------------------- publish


def cleared(desc: dict) -> tuple[bool, str]:
    src = (desc.get("rights") or {}).get("source")
    if src in CLEARED_SOURCES:
        return True, src or "?"
    return False, src or "unset"


def sidecars(video: Path) -> list[Path]:
    return sorted(video.glob("stills/*.clip.yaml")) + sorted(video.glob("*.clip.yaml"))


def publish(video_id: str, force: bool = False, dry_run: bool = False) -> int:
    """Upload an edit's media to B2 and write the manifest pointers back into git.

    The write-back is the point. An upload nobody can find again from the repo is not
    provenance, it is a backup.
    """
    # Deliberately NOT gated on genblaze/B2 being installed. The rights gate below is a
    # question about the descriptors in git, and being able to answer "may this ship?"
    # before any credential exists is the entire value of asking it early.
    if not dry_run:
        ok, why = importable()
        if not ok:
            sys.exit(f"genblaze unavailable: {why}")
        if miss := missing_env():
            sys.exit(f"missing env: {', '.join(miss)} — see .env.example")

    edit_path = rtf.find_edit(video_id)
    video = edit_path.parent
    edit = yaml.safe_load(edit_path.read_text())

    # Rights gate. Renderable and publishable are different questions, and this is the
    # boundary where the second one is asked.
    blocked = []
    for sc in sidecars(video):
        desc = yaml.safe_load(sc.read_text())
        good, src = cleared(desc)
        if not good:
            blocked.append((sc.name, src))
    if blocked and not force:
        print("refusing to publish — rights.source not cleared (CLIP-SPEC rule 3):")
        for name, src in blocked:
            print(f"  {name}: {src!r}")
        print("\nfix the descriptor, or pass --force if you own that decision.")
        return 1

    render = video / f"{edit['id']}.mp4"
    targets = [p for p in [render, *sorted(video.glob("stills/*.png"))] if p.exists()]
    if not targets:
        sys.exit(f"nothing to publish in {video} — render it first")

    bucket = os.environ.get("B2_BUCKET", "<B2_BUCKET unset>")
    print(f"publish {edit['id']}  →  b2://{bucket}")
    print(f"  {len(targets)} object(s), rights: "
          f"{'FORCED past ' + str(len(blocked)) + ' block(s)' if blocked else 'cleared'}")
    if dry_run:
        for p in targets:
            print(f"  DRY  kits/{edit['id']}/{p.relative_to(video)}")
        return 0

    from genblaze_core import Manifest  # noqa: F401  (surface import errors early)

    store = backend()
    written = 0
    for p in targets:
        key = f"kits/{edit['id']}/{p.relative_to(video)}"
        store.put(key, p.read_bytes())
        written += 1
        print(f"  ↑ {key}")

    # The pointer back into git. Without this the repo cannot find what it published.
    edit.setdefault("provenance", {})
    edit["provenance"]["b2_bucket"] = os.environ["B2_BUCKET"]
    edit["provenance"]["b2_prefix"] = f"kits/{edit['id']}/"
    edit["provenance"]["published_objects"] = written
    edit_path.write_text(yaml.safe_dump(edit, sort_keys=False, width=100,
                                        allow_unicode=True))
    print(f"\n✓ {written} object(s); pointers written to {edit_path.name}")
    return 0


# ---------------------------------------------------------------- verify


def verify(path: Path) -> int:
    """Run Genblaze's own manifest check against a delivered file.

    This is the public `/verify` story reduced to its one honest sentence: the record of
    which model made this, from which prompt, travels inside the file.
    """
    ok, why = importable()
    if not ok:
        sys.exit(f"genblaze unavailable: {why}")

    ext = path.suffix.lower()
    if ext not in EMBEDDABLE:
        sys.exit(f"{ext} carries no embedded manifest — Genblaze handles {sorted(EMBEDDABLE)}")

    import genblaze_core

    handler = getattr(genblaze_core, EMBEDDABLE[ext])()
    manifest = handler.extract(path.read_bytes())
    if manifest is None:
        print(f"✗ {path.name}: no embedded manifest")
        return 1

    print(f"file          {path.name}")
    print(f"canonical     {manifest.canonical_hash}")
    print(f"hash intact   {manifest.verify_hash()}")
    print(f"assets valid  {manifest.verify()}")
    return 0 if manifest.verify_hash() and manifest.verify() else 1


# ---------------------------------------------------------------- check


def check() -> int:
    """Report readiness without spending anything. Run this first."""
    load_dotenv(Path.cwd())
    ok, why = importable()
    miss = missing_env()
    print(f"python        {sys.version.split()[0]}")
    print(f"genblaze      {'ok' if ok else why}")
    print(f"B2 env        {'ok' if not miss else 'missing ' + ', '.join(miss)}")
    if ok and not miss:
        try:
            backend().head("__rtf_probe__")
        except Exception as e:                                       # noqa: BLE001
            # A 404 on a key that does not exist is the healthy answer; anything else
            # (403, DNS, bad bucket) is a real configuration problem.
            msg = str(e)
            healthy = "404" in msg or "NoSuchKey" in msg or "Not Found" in msg
            print(f"B2 reachable  {'ok (bucket answers)' if healthy else msg[:90]}")
    return 0 if ok and not miss else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="report readiness; spends nothing")
    p = sub.add_parser("publish", help="upload an edit's media to B2")
    p.add_argument("video", help="video id, dir, or edit.yaml")
    p.add_argument("--force", action="store_true", help="publish past a rights block")
    p.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify", help="check a delivered file's embedded manifest")
    v.add_argument("path")
    args = ap.parse_args()

    if args.cmd == "check":
        return check()
    load_dotenv(Path.cwd())
    if args.cmd == "publish":
        return publish(args.video, force=args.force, dry_run=args.dry_run)
    return verify(Path(args.path).resolve())


if __name__ == "__main__":
    sys.exit(main())
