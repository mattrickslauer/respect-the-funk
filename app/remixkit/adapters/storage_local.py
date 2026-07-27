"""Filesystem storage — the zero-credential path.

Two jobs. It satisfies our own `ports.Storage`, and (via `as_genblaze_backend`) it
satisfies Genblaze's `StorageBackend` too, so the real `ObjectStorageSink` and its real
manifest writing can run on a laptop with no bucket. That matters more than it sounds:
it means the manifest we verify in dev is produced by the same code path that produces
the one in B2, rather than by a fake.

Presigned URLs have no meaning on a filesystem, so they degrade to app-served routes.
This is the one place the "bytes never transit the app server" rule is knowingly
broken, and it is bounded to `storage_backend=local`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote


class LocalStorage:
    name = "local"

    def __init__(self, root: Path, *, url_base: str = "/files") -> None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".writable"
            probe.write_text("ok")
            probe.unlink()
        except OSError:
            # Read-only deployment filesystem (Lambda, Vercel). /tmp is writable and
            # ephemeral, which is correct for a demo and wrong for anything durable —
            # hence the warning surfaced on the health endpoint.
            root = Path(tempfile.gettempdir()) / "remixkit-data"
            root.mkdir(parents=True, exist_ok=True)
            self.ephemeral = True
        else:
            self.ephemeral = False
        self.root = root
        self.url_base = url_base

    def _path(self, key: str) -> Path:
        # Reject traversal before it becomes a file write outside the root.
        parts = [p for p in key.split("/") if p not in ("", ".", "..")]
        return self.root.joinpath(*parts)

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """Atomic write — temp file in the same directory, then `os.replace`.

        Not a nicety. The document repository reads these back constantly, and a kit
        being updated by the worker while the console polls it is the normal case. A
        plain `write_bytes` lets a reader observe a truncated file, which surfaces as a
        validation error on a document that is actually fine. `os.replace` is atomic on
        the same filesystem, so a reader sees either the old bytes or the new ones.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        )

    def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        return f"{self.url_base}/{quote(key)}"

    def presign_put(self, key: str, *, expires_in: int = 3600, content_type: str | None = None) -> str:
        return f"{self.url_base}/{quote(key)}"

    def key_from_url(self, url: str) -> str | None:
        path = unquote(url.split("?", 1)[0])
        if "://" in path:
            path = "/" + path.split("://", 1)[1].split("/", 1)[-1]
        prefix = self.url_base.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix) :]
        return path.lstrip("/") or None

    def as_genblaze_backend(self):
        """Adapt to Genblaze's `StorageBackend` so the real sink works here."""
        return _GenblazeLocalBackend(self)


class _GenblazeLocalBackend:
    """Genblaze's StorageBackend surface: put/get/exists/delete/get_url/get_durable_url.

    Genblaze declares these six abstract; we implement them over `LocalStorage` rather
    than subclassing, to avoid importing genblaze at module scope (the app must import
    cleanly whether or not the extras are installed).
    """

    def __init__(self, storage: LocalStorage) -> None:
        self._s = storage

    def put(self, key: str, data: bytes, *, content_type: str | None = None, **kwargs) -> str:
        return self._s.put(key, data, content_type=content_type)

    def get(self, key: str, **kwargs) -> bytes:
        return self._s.get(key)

    def exists(self, key: str, **kwargs) -> bool:
        return self._s.exists(key)

    def delete(self, key: str, **kwargs) -> None:
        self._s.delete(key)

    def get_url(self, key: str, **kwargs) -> str:
        return self._s.presign_get(key)

    def get_durable_url(self, key: str, **kwargs) -> str:
        return self._s.presign_get(key)

    def close(self) -> None:
        """Genblaze closes the backend during sink teardown. A filesystem has nothing
        to release, but the method must exist or teardown raises after a successful run."""
        return None
