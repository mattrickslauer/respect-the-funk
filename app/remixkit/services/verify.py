"""Provenance verification — the one piece of role 3 that was kept.

PRODUCT.md defers the ops/judge role "except `/verify`", because it is a single
read-only operation that buys a named hackathon scoring category for a few hours of
work. This is that operation.

Two inputs are accepted, and the distinction is the interesting part:

* **A manifest** — parse and check its signature and canonical hash.
* **A media file** — read the manifest *embedded in the file itself*. This is the
  disclosure argument made literal: when an asset leaves the system, the record of
  which model made it, from which prompt, for which release travels inside the bytes.
  Nothing has to be looked up, and nothing can be quietly stripped without the check
  failing.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class VerificationReport:
    verified: bool
    source: str  # "embedded" | "manifest" | "none"
    detail: str
    run_id: str | None = None
    tenant_id: str | None = None
    canonical_hash: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VerifyService:
    def verify_bytes(self, data: bytes, filename: str = "") -> VerificationReport:
        manifest = self._extract_embedded(data, filename)
        source = "embedded"
        if manifest is None:
            manifest = self._parse_manifest(data)
            source = "manifest"
        if manifest is None:
            return VerificationReport(
                verified=False,
                source="none",
                detail=(
                    "No Genblaze manifest found — neither embedded in the file nor as a "
                    "manifest document. This asset carries no provenance."
                ),
            )

        try:
            verified = bool(manifest.verify())
        except Exception as exc:
            log.warning("verify raised: %s", exc)
            return VerificationReport(
                verified=False, source=source, detail=f"Verification error: {exc}"
            )

        run = getattr(manifest, "run", None)
        steps = [
            {
                "provider": str(s.provider),
                "model": str(s.model),
                "modality": str(s.modality),
                "prompt": s.prompt,
                "status": str(s.status),
            }
            for s in (getattr(run, "steps", None) or [])
        ]

        return VerificationReport(
            verified=verified,
            source=source,
            detail=(
                "Manifest verified — the signature and canonical hash match the recorded run."
                if verified
                else "Manifest found but verification failed. Treat its provenance claim as unsupported."
            ),
            run_id=str(getattr(run, "run_id", "")) or None,
            tenant_id=str(getattr(run, "tenant_id", "") or "") or None,
            canonical_hash=getattr(manifest, "canonical_hash", None),
            steps=steps,
        )

    # -- extraction -------------------------------------------------------------
    @staticmethod
    def _extract_embedded(data: bytes, filename: str):
        """Pull the manifest back out of the media file, if it is in there.

        Genblaze dispatches per media type rather than offering one extract-anything
        call: `sniff_mime` reads magic bytes, `get_handler` maps the type to a handler,
        and the handler works on a `Path`. So the upload is written to a temp file for
        the duration of the check — content sniffing rather than trusting the uploaded
        filename, which is the only safe way to pick a parser for a file a stranger sent.
        """
        try:
            from genblaze_core.media import get_handler, guess_mime, sniff_mime
        except Exception:
            return None

        suffix = Path(filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            temp_path = Path(handle.name)
        try:
            mime = sniff_mime(temp_path) or guess_mime(temp_path)
            if not mime:
                return None
            handler = get_handler(mime)
            if handler is None:
                return None
            return handler.extract(temp_path)
        except Exception:
            # No embedded manifest is the common case, not an error — a plain asset
            # simply has nothing to extract.
            return None
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _parse_manifest(data: bytes):
        """`parse_manifest` takes a already-decoded dict, not raw JSON text."""
        try:
            from genblaze_core import parse_manifest
        except Exception:
            return None
        try:
            return parse_manifest(json.loads(data.decode("utf-8")))
        except Exception:
            return None
