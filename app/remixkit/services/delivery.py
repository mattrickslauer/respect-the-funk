"""Delivery — the asset that leaves the system carries its own provenance.

BUILD-SPEC §5's "provenance = disclosure", implemented at the only moment it matters:
the download. The stored object is the raw provider output; the *delivered* copy has
the run's Genblaze manifest embedded in the file itself.

Why this is the right seam rather than embedding at generation time:

* The manifest describes the whole run, and the run is not finished while its steps
  are still producing. Embedding on delivery is the first point where the thing being
  embedded is complete and verified.
* The stored object stays byte-identical to what the provider returned, which is what
  makes the content hash in the manifest mean anything.

So when someone downloads a clip, the record of which model made it, from which prompt,
for which release travels inside the media — and `/verify` reads it back out of the
bytes with nothing to look up. Strip it and the check fails; that is the point.

An asset whose media type has no embedding handler (an MP3 without `mutagen`, say) is
delivered unmodified rather than blocked, and the caller is told which happened —
silently handing back an undisclosed file would be the one failure mode this whole
service exists to prevent.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from remixkit.auth.provider import Principal
from remixkit.domain.models import Kit, slugify
from remixkit.ports.storage import Storage
from remixkit.services.errors import NotFound
from remixkit.services.kits import KitService

log = logging.getLogger(__name__)


@dataclass
class Delivery:
    data: bytes
    filename: str
    media_type: str
    manifest_embedded: bool
    note: str | None = None


class DeliveryService:
    def __init__(self, storage: Storage, kits: KitService) -> None:
        self._storage = storage
        self._kits = kits

    def asset(self, principal: Principal, kit_id: str, asset_id: str) -> Delivery:
        kit = self._kits.get(principal, kit_id)
        asset = next((a for a in kit.assets if a.id == asset_id), None)
        if asset is None or not asset.key:
            raise NotFound(f"No asset {asset_id!r} on kit {kit_id!r}")

        raw = self._storage.get(asset.key)
        suffix = Path(asset.key).suffix or ""
        # Slugified, not just lowercased: kit names are free text and default to
        # "<title> — kit", and an em dash in a Content-Disposition header is a
        # latin-1 encoding error, i.e. a 500 on the download path.
        filename = f"{slugify(kit.name)}-{asset_id[-6:]}{suffix}"
        media_type = _media_type(suffix)

        manifest = self._load_manifest(kit)
        if manifest is None:
            return Delivery(
                data=raw,
                filename=filename,
                media_type=media_type,
                manifest_embedded=False,
                note="No manifest was available for this run — delivered without embedded provenance.",
            )

        embedded, note = self._embed(raw, suffix, manifest)
        return Delivery(
            data=embedded if embedded is not None else raw,
            filename=filename,
            media_type=media_type,
            manifest_embedded=embedded is not None,
            note=note,
        )

    # -- internals --------------------------------------------------------------
    def _load_manifest(self, kit: Kit):
        if not kit.manifest_key:
            return None
        try:
            from genblaze_core import parse_manifest

            return parse_manifest(json.loads(self._storage.get(kit.manifest_key).decode()))
        except Exception as exc:
            log.warning("kit %s: could not load manifest %s (%s)", kit.id, kit.manifest_key, exc)
            return None

    @staticmethod
    def _embed(raw: bytes, suffix: str, manifest) -> tuple[bytes | None, str | None]:
        try:
            from genblaze_core.media import SmartEmbedder
        except Exception:
            return None, "Embedding is unavailable in this build."

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / f"asset{suffix or '.bin'}"
            source.write_bytes(raw)
            output = Path(tmp) / f"delivered{suffix or '.bin'}"
            try:
                SmartEmbedder().embed(source, manifest, output)
            except Exception as exc:
                return None, f"No embedding handler for this media type — delivered as-is ({exc})."
            if not output.exists():
                return None, "Embedding produced no output — delivered as-is."
            return output.read_bytes(), None


def _media_type(suffix: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
    }.get(suffix.lower(), "application/octet-stream")
