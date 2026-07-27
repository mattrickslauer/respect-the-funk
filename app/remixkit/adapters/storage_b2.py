"""Backblaze B2 via Genblaze's own S3 backend.

Thin on purpose. `S3StorageBackend.for_backblaze` already does the endpoint discovery,
the presigning, and the user-agent Genblaze wants; wrapping it in more than an adapter
would be inventing a second opinion about B2. All this class adds is our `ports.Storage`
shape and a `list()` that tolerates the backend's paging.

Requires the `b2` extra (`genblaze-s3`) and `B2_KEY_ID` / `B2_APP_KEY`.
"""

from __future__ import annotations


class B2Storage:
    name = "b2"

    def __init__(
        self,
        bucket: str,
        *,
        key_id: str = "",
        app_key: str = "",
        region: str = "",
    ) -> None:
        try:
            from genblaze_s3 import S3StorageBackend
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "storage_backend=b2 needs the 'b2' extra: pip install 'remixkit[b2]'"
            ) from exc

        kwargs: dict[str, object] = {"bucket": bucket}
        if key_id:
            kwargs["key_id"] = key_id
        if app_key:
            kwargs["app_key"] = app_key
        if region:
            kwargs["region"] = region
        self._backend = S3StorageBackend.for_backblaze(**kwargs)  # type: ignore[arg-type]

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        self._backend.put(key, data, content_type=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self._backend.get(key)

    def exists(self, key: str) -> bool:
        return self._backend.exists(key)

    def delete(self, key: str) -> None:
        self._backend.delete(key)

    def list(self, prefix: str) -> list[str]:
        return [
            item if isinstance(item, str) else getattr(item, "key", str(item))
            for item in self._backend.list(prefix)
        ]

    def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        return self._backend.presigned_get_url(key, expires_in=expires_in)

    def presign_put(self, key: str, *, expires_in: int = 3600, content_type: str | None = None) -> str:
        return self._backend.presigned_put_url(key, expires_in=expires_in)

    def key_from_url(self, url: str) -> str | None:
        """The S3 backend already knows how to invert its own presigned URLs."""
        try:
            return self._backend.key_from_url(url)
        except Exception:
            return None

    def as_genblaze_backend(self):
        """The sink takes the underlying backend directly — no double wrapping."""
        return self._backend
