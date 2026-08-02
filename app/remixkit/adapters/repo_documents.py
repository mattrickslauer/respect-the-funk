"""Documents over the storage port — this is the "there is no database" decision, coded.

infra/README argues the label's data is documents, not relations: an artist, an
identity, a song's measurements, a kit. So this repository is a thin YAML-over-objects
store, and the storage port underneath it is the only thing that changes between a
laptop directory and a B2 bucket.

The key layout mirrors PRODUCT.md's proposed tree and B2's hierarchy at once:

    {prefix}/tenants/{tenant_id}/{collection}/{doc_id}.yaml

YAML rather than JSON because the repo's whole sidecar convention is YAML and a human
opening one of these in the bucket should see the same thing they see in `content/lib`.

Two honest limits, stated rather than discovered later:
  * `list()` is a prefix scan and deserialises everything it finds — fine at roster
    scale (tens to low thousands), not a query engine. The moment it needs sorting or
    filtering server-side, that is the signal infra/README names for revisiting the tier.
  * Writes are last-write-wins with no compare-and-set. Single-editor console today;
    concurrent editors would need a conditional put.
"""

from __future__ import annotations

from typing import TypeVar

import yaml
from pydantic import BaseModel

from remixkit.ports.storage import Storage

T = TypeVar("T", bound=BaseModel)


def _is_missing(exc: Exception) -> bool:
    """Did this read fail because the object is not there — or because nobody answered?

    The two storage adapters say it differently and neither is imported here, because the
    point of the port is that this file does not know which one it has. A filesystem raises
    `FileNotFoundError`; the S3/B2 backend raises a `StorageError` it has already
    classified, and `NOT_FOUND` is the one classification that means absent.

    Anything unrecognised is *not* a miss. That default is the whole fix: read this
    generously and a capped bucket, an expired key or a network partition all become "no
    such song", which is the failure that took a day to diagnose because it accused the
    catalogue instead of the storage.
    """
    if isinstance(exc, FileNotFoundError):
        return True
    return getattr(getattr(exc, "error_code", None), "name", "") == "NOT_FOUND"


class DocumentRepo:
    name = "documents"

    def __init__(self, storage: Storage, *, prefix: str = "remixkit") -> None:
        self._storage = storage
        self._prefix = prefix.strip("/")

    def _key(self, tenant_id: str, collection: str, doc_id: str) -> str:
        return f"{self._prefix}/tenants/{tenant_id}/{collection}/{doc_id}.yaml"

    def _collection_prefix(self, tenant_id: str, collection: str) -> str:
        return f"{self._prefix}/tenants/{tenant_id}/{collection}"

    def put(self, tenant_id: str, collection: str, doc_id: str, doc: BaseModel) -> None:
        payload = yaml.safe_dump(
            doc.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ).encode()
        self._storage.put(
            self._key(tenant_id, collection, doc_id),
            payload,
            content_type="application/yaml",
        )

    def get(self, tenant_id: str, collection: str, doc_id: str, model: type[T]) -> T | None:
        """The document, or `None` when the object genuinely is not there.

        One read, not two. The `exists()`-then-`get()` pair this replaces cost two billable
        operations per document and answered the wrong question with the first of them: B2
        returns **403**, not 404, once a daily cap is hit, and the S3 backend's `exists()`
        deliberately maps 403 to `False` — a real accommodation for scoped keys that hold
        `ReadFiles` without `ListFiles`, where HEAD on a missing key answers 403.

        Downstream, `None` means `NotFound` means **404**, so a capped bucket presented as
        a console full of songs that had stopped existing. Every signal pointed away from
        storage: the object was in the bucket, the roster still listed it (`list()` is a
        different transaction class, with its own cap), and nothing in the app's own logs
        mentioned B2.

        So absence is now something only the vendor gets to assert, by classifying the read
        `NOT_FOUND`. Anything else — a cap, an expired key, a bucket that has stopped
        answering — is raised, because a storage outage reported as an empty catalogue is
        the one failure nobody can act on.
        """
        raw = self._read(self._key(tenant_id, collection, doc_id))
        if raw is None:
            return None
        return model.model_validate(yaml.safe_load(raw))

    def list(self, tenant_id: str, collection: str, model: type[T]) -> list[T]:
        out: list[T] = []
        for key in self._storage.list(self._collection_prefix(tenant_id, collection)):
            if not key.endswith(".yaml"):
                continue
            raw = self._read(key)
            if raw is None:
                # Listed a moment ago and gone now. A roster is a snapshot, not a lock.
                continue
            try:
                out.append(model.model_validate(yaml.safe_load(raw)))
            except Exception:
                # A malformed sidecar must not take down the roster listing. It stays
                # invisible in the UI rather than 500-ing the page that would let
                # someone fix it.
                #
                # Narrowed to *parsing*: this used to wrap the read as well, so a bucket
                # refusing every object rendered as a roster that was simply empty. A
                # document nobody can parse is one broken row; a bucket nobody can read is
                # not a catalogue with nothing in it.
                continue
        return out

    def _read(self, key: str) -> bytes | None:
        """`storage.get`, with a genuine miss told apart from a refusal to answer."""
        try:
            return self._storage.get(key)
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise

    def delete(self, tenant_id: str, collection: str, doc_id: str) -> None:
        self._storage.delete(self._key(tenant_id, collection, doc_id))
