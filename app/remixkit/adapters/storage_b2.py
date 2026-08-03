"""Backblaze B2 via Genblaze's own S3 backend.

Thin on purpose. `S3StorageBackend.for_backblaze` already does the endpoint discovery,
the presigning, and the user-agent Genblaze wants; wrapping it in more than an adapter
would be inventing a second opinion about B2. All this class adds is our `ports.Storage`
shape and a `list()` that tolerates the backend's paging.

Requires the `b2` extra (`genblaze-s3`) and `B2_KEY_ID` / `B2_APP_KEY`.
"""

from __future__ import annotations

import time

# How much of a presigned URL's life it is served for before being re-signed. The
# remainder is deliberate slack: a page rendered at the last moment of reuse still holds a
# URL valid for the rest of the hour, so nothing expires while somebody is looking at it.
_PRESIGN_REUSE = 0.5

# Backblaze's own default is the reason these constants exist. Per B2's *Configure
# Cache-Control Policies with the Native API*: buckets created on or after 2021-09-08 send
# **no** `Cache-Control` header at all, where buckets older than that defaulted to
# `max-age=0, no-cache, no-store`. Either way nothing downstream is told it may keep a
# copy, and a presigned URL carries a query string — which browsers will not heuristically
# cache. So the stable-URL work in `presign_get` below bought a cache key and then never
# handed the browser permission to use it.
#
# Genblaze already sets this on the assets *it* writes — `storage/transfer.py` picks
# `private, max-age=3600` for `HIERARCHICAL` keys, which is the layout this app uses. What
# it does not cover is everything that reaches B2 through this adapter instead of through
# the sink: song masters, identity reference frames, scored cuts. Those arrived with no
# policy at all. This closes that gap and deliberately matches the library's own value
# rather than inventing a longer one.
#
# `private` because every object here is tenant media behind a presigned URL: the browser
# that asked for it may keep it, shared caches may not. `max-age=3600` because that is the
# presign lifetime — caching past the point where the URL stops working buys nothing, and
# `_PRESIGN_REUSE` hands the same URL back for the first half of it, so a re-render inside
# the window is a cache hit rather than a download.
#
# `immutable` is the part that pays the sponsor bill. Without it a browser holding a fresh
# copy still issues a conditional revalidation, and on B2 that request is a Class B
# transaction against the daily cap — the same cap `presign_get` was written to protect.
# With it, the revalidation is not sent.
_ASSET_CACHE = "private, max-age=3600, immutable"

# Documents are the one thing here that is overwritten in place — `repo_documents` is
# last-write-wins at `{prefix}/tenants/{tenant}/{collection}/{id}.yaml`. They are read
# server-side by `get()` and never presigned to a browser, so this is belt-and-braces
# rather than a live bug: if one is ever served directly, it must not be an hour stale.
_DOCUMENT_CACHE = "no-cache"


def _cache_control_for(key: str) -> str:
    """The `Cache-Control` an object at `key` should be stored with.

    `immutable` is safe for the mutable media keys too, which is the non-obvious part. A
    master re-uploaded over its own key would be a stale-cache bug if the URL stayed the
    same — but `put` calls `_forget`, so the next `presign_get` signs a *fresh* wall clock
    and mints a URL the browser has never seen. Immutable content under a rotating name:
    the old cache entry is abandoned rather than wrongly reused.
    """
    return _DOCUMENT_CACHE if key.endswith(".yaml") else _ASSET_CACHE


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
        # key -> (url, monotonic deadline for reuse). Per process: a worker signing its
        # own URLs is still one download per object per window, which is the whole point.
        self._presigned: dict[str, tuple[str, float]] = {}

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        self._backend.put(
            key,
            data,
            content_type=content_type,
            # `CacheControl` is a plain boto3 ExtraArgs key, so the S3-compatible endpoint
            # carries it to the same place `X-Bz-Info-b2-cache-control` would on the
            # native API. Nothing here needs the native client.
            extra_args={"CacheControl": _cache_control_for(key)},
        )
        self._forget(key)
        return key

    def get(self, key: str) -> bytes:
        return self._backend.get(key)

    def exists(self, key: str) -> bool:
        return self._backend.exists(key)

    def delete(self, key: str) -> None:
        self._backend.delete(key)
        self._forget(key)

    def list(self, prefix: str) -> list[str]:
        """Every key under `prefix`, following pagination to the end.

        Two things the local backend could not have caught, both found against real B2:
        `list()` returns a `ListPage` (a frozen dataclass of `entries` + `next_token`),
        not an iterable of keys — iterating the page itself raises `TypeError`. And it
        pages at 1000 keys, so a single call silently truncates. The document repository
        builds the entire roster from this, so a truncated listing would present as
        artists that simply vanish once the catalogue grows past a page.
        """
        keys: list[str] = []
        token: str | None = None
        while True:
            page = self._backend.list(prefix, continuation_token=token)
            keys.extend(entry.key for entry in page.entries)
            token = page.next_token
            if token is None:
                return keys

    # Absolute, signed, and fetchable by anyone holding the URL for its lifetime — which
    # is what makes conditioning images possible at all: the provider does the fetching.
    serves_public_urls = True

    def presign_get(self, key: str, *, expires_in: int = 3600) -> str:
        """A playable URL for this key — the *same* URL for every render in a window.

        SigV4 signs the wall clock to the second, so presigning twice a second apart
        returns two different URLs for the same unchanged object. To a browser those are
        two resources, and its cache is addressed by URL: every render of a page re-fetched
        every image, every clip and the master in full, because none of them had ever been
        asked for before under that name.

        That is what emptied the daily cap. The bucket holds tens of megabytes and B2's
        free tier allows a gigabyte a day, which sounds like plenty right up until each
        page view spends five of those megabytes again on a 5MB reference frame that has
        not changed since it was uploaded.

        So a minted URL is kept and handed back until it is close enough to expiry to be
        worth replacing — `_PRESIGN_REUSE` of its life, leaving the rest as the window in
        which a page that has already rendered can still load what it points at. Stable
        URL, working cache, one download per object per window.

        Correctness rests on keys being immutable in practice: frames are content-hashed,
        run assets are per-run UUIDs. Where that does not hold, `put` and `delete` drop the
        entry, so a re-uploaded master is re-signed rather than served from a stale cache.
        """
        cached = self._presigned.get(key)
        now = time.monotonic()
        if cached is not None and now < cached[1]:
            return cached[0]
        url = self._backend.presigned_get_url(key, expires_in=expires_in)
        self._presigned[key] = (url, now + expires_in * _PRESIGN_REUSE)
        return url

    def _forget(self, key: str) -> None:
        """Drop a cached URL, so the next render signs the object as it is now."""
        self._presigned.pop(key, None)

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
