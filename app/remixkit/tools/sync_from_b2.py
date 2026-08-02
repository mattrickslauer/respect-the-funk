"""Pull a tenant's objects out of B2 onto local disk, once, so dev can stop reading B2.

`RK_STORAGE_BACKEND=local` costs nothing and shows nothing: every artist, song, identity
and kit lives in the bucket, so a laptop pointed at a directory starts with an empty
catalogue. This closes that gap by paying for the whole tenant **once** instead of paying
for part of it on every page view.

The arithmetic is the entire argument. B2 bills transactions rather than bytes and allows
2,500 Class B a day; a console page costs roughly 25 of them, so about a hundred page
loads a day. One full sync of this tenant is ~45 reads — under two page views — and after
it nothing is spent again until you ask for it. That is the difference between a daily cap
that is a wall and one that is irrelevant.

**One direction, enforced.** This reads B2 and writes local disk, and never the reverse.
The local copy is a working convenience, not a replica: it is not written back, not
reconciled, and not authoritative for anything. If dev diverges, delete the directory and
run this again. Anything that made the local copy a candidate for upload would turn a
laptop into a way to corrupt production, which is a much worse failure than an empty
console.

Usage:

    python -m remixkit.tools.sync_from_b2 --dry-run      # what it would cost
    python -m remixkit.tools.sync_from_b2               # documents only
    python -m remixkit.tools.sync_from_b2 --media       # ...plus frames, masters, assets
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger("remixkit.sync")

# Objects whose absence merely makes the console *look* wrong, versus objects whose absence
# makes it *empty*. Documents are the catalogue; everything else is what the catalogue
# points at, and it is two orders of magnitude larger.
DOCUMENT_SUFFIX = ".yaml"


def _human(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def _plan(source, prefixes: list[str], media: bool) -> list[str]:
    """Every key worth copying, listed rather than guessed.

    Listing is a Class *C* transaction and reading is Class B, which is the whole reason
    the plan is built first: a listing costs one call per thousand keys and tells us
    exactly what the sync will spend before a single billable read happens.
    """
    keys: list[str] = []
    for prefix in prefixes:
        for key in source.list(prefix):
            if not media and not key.endswith(DOCUMENT_SUFFIX):
                continue
            keys.append(key)
    return sorted(set(keys))


class SyncResult(NamedTuple):
    """What the run actually did. `failed` is the field that earns the type.

    The first version returned three numbers and none of them was failures, so a run in
    which the cap refused every single object printed `copied 0, skipped 0 already local`
    — true, cheerful, and wrong about what happened. The per-object warnings were there to
    read, but a summary that does not mention twenty failures is a summary that says the
    tenant was empty. The one condition this tool was built to survive is the one its own
    report glossed over.
    """

    copied: int
    skipped: int
    failed: int
    total_bytes: int


def sync(
    source,
    destination,
    prefixes: list[str],
    *,
    media: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> SyncResult:
    """Copy `prefixes` from `source` to `destination`.

    Already-present objects are skipped without being read, which is what makes this safe
    to re-run: a second sync after adding one song costs one Class B, not forty-five. The
    check is presence rather than content, because the keys here are effectively immutable
    — content-hashed frames, per-run asset UUIDs, and documents that are rewritten under
    the same name only by the app itself. `--force` is for when that assumption breaks.
    """
    keys = _plan(source, prefixes, media)
    copied = skipped = failed = total = 0

    for key in keys:
        if not force and destination.exists(key):
            skipped += 1
            continue
        if dry_run:
            copied += 1
            continue
        try:
            data = source.get(key)
        except Exception as exc:
            # One unreadable object must not cost the other forty-four. A cap reached
            # mid-sync is the likeliest cause, and the run is resumable by design — but it
            # is counted, because a failure nobody totals is a failure nobody notices.
            failed += 1
            log.warning("could not read %s (%s)", key, exc)
            continue
        destination.put(key, data)
        copied += 1
        total += len(data)
        log.info("%s (%s)", key, _human(len(data)))

    return SyncResult(copied, skipped, failed, total)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_from_b2",
        description="Copy a tenant's B2 objects to the local storage directory, one way.",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="Tenant to copy. Defaults to RK_DEFAULT_TENANT_ID.",
    )
    parser.add_argument(
        "--media",
        action="store_true",
        help="Also copy frames, masters and run assets — the bytes, not just the catalogue.",
    )
    parser.add_argument(
        "--runs",
        action="store_true",
        help="Also copy generated run output (implies --media for those keys).",
    )
    parser.add_argument("--force", action="store_true", help="Re-copy objects already local.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be copied and what it would cost. Reads nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from remixkit.adapters.storage_b2 import B2Storage
    from remixkit.adapters.storage_local import LocalStorage
    from remixkit.bootstrap import load_secrets
    from remixkit.settings import get_settings

    settings = get_settings()
    if load_secrets(settings.ssm_path, profile=settings.aws_profile):
        get_settings.cache_clear()
        settings = get_settings()

    if not settings.b2_bucket:
        print(
            "No B2 bucket configured. This reads RK_B2_BUCKET and the SSM path from your "
            ".env, which stay filled in even when RK_STORAGE_BACKEND=local.",
            file=sys.stderr,
        )
        return 2

    # Built directly rather than through the container, because the container honours
    # RK_STORAGE_BACKEND — which is `local` precisely when this script is worth running.
    # Both ends are needed at once, so neither can be "the" configured backend.
    source = B2Storage(
        settings.b2_bucket,
        key_id=settings.b2_key_id,
        app_key=settings.b2_app_key,
        region=settings.b2_region,
    )
    destination = LocalStorage(Path(settings.local_storage_dir))

    tenant = args.tenant or settings.default_tenant_id
    prefix = settings.key_prefix.strip("/")
    prefixes = [f"{prefix}/tenants/{tenant}"]
    if args.runs:
        prefixes.append(f"{prefix}/runs/{tenant}")

    print(f"B2 {settings.b2_bucket!r} → {destination.root}")
    print(f"tenant {tenant!r}, {'documents + media' if args.media else 'documents only'}")

    result = sync(
        source,
        destination,
        prefixes,
        media=args.media or args.runs,
        dry_run=args.dry_run,
        force=args.force,
    )

    if args.dry_run:
        print(
            f"\nwould copy {result.copied} object(s), skip {result.skipped} already local.\n"
            f"cost: ~{result.copied} Class B transactions of the 2,500 daily allowance."
        )
        return 0

    print(
        f"\ncopied {result.copied} object(s) ({_human(result.total_bytes)}), "
        f"skipped {result.skipped} already local, failed {result.failed}."
    )
    if result.copied:
        print("Restart the app — the console now reads this from disk and B2 is untouched.")
    if result.failed:
        # Non-zero, because the likeliest cause is the daily cap and the likeliest next
        # move is to run this again later. Saying so beats leaving somebody to notice that
        # a "successful" sync produced an empty directory.
        print(
            f"\n{result.failed} object(s) could not be read — see the warnings above. If that "
            "is the B2 cap, the counters reset at UTC midnight; re-run then. Nothing already "
            "copied will be paid for twice."
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover — entry point
    raise SystemExit(main())
