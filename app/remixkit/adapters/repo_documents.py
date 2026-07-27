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
        key = self._key(tenant_id, collection, doc_id)
        if not self._storage.exists(key):
            return None
        return model.model_validate(yaml.safe_load(self._storage.get(key)))

    def list(self, tenant_id: str, collection: str, model: type[T]) -> list[T]:
        out: list[T] = []
        for key in self._storage.list(self._collection_prefix(tenant_id, collection)):
            if not key.endswith(".yaml"):
                continue
            try:
                out.append(model.model_validate(yaml.safe_load(self._storage.get(key))))
            except Exception:
                # A malformed sidecar must not take down the roster listing. It stays
                # invisible in the UI rather than 500-ing the page that would let
                # someone fix it.
                continue
        return out

    def delete(self, tenant_id: str, collection: str, doc_id: str) -> None:
        self._storage.delete(self._key(tenant_id, collection, doc_id))
