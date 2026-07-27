"""Bytes in, bytes out, plus the two presigned methods that keep them off the app server.

`presign_get` / `presign_put` are not a convenience — they are BUILD-SPEC §2b rule 2
("nothing large ever transits the app server") expressed as an interface. A masters
upload and a kit download both go browser↔bucket directly; FastAPI only ever moves the
JSON that describes them.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """Write bytes, return the key."""

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def list(self, prefix: str) -> list[str]: ...

    def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        """A URL the browser can GET directly. Never proxy the bytes."""

    def presign_put(self, key: str, *, expires_in: int = 3600, content_type: str | None = None) -> str:
        """A URL the browser can PUT directly. Never proxy the bytes."""

    def key_from_url(self, url: str) -> str | None:
        """Invert the URL back to an object key.

        Needed because Genblaze reports what it wrote as a URL, and every backend
        builds that URL differently — a presigned B2 host with a query string, or a
        local `/files/…` route. Guessing with string surgery works for one of those
        and silently produces a wrong key for the other, so each adapter inverts its
        own scheme.
        """
