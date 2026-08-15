"""Document persistence, tenant-scoped by construction.

There is no `tenant_id` filter to forget here: it is a positional argument on every
method, so an un-scoped read does not typecheck and barely parses. infra/README's
"the move that made it simple: there is no database" survives only if this interface
stays document-shaped — get/put/list by key, no joins, no queries. The moment
something needs a `WHERE … ORDER BY`, that is the signal to revisit the tier, not to
grow a query language here.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class DocumentRepository(Protocol):
    def put(self, tenant_id: str, collection: str, doc_id: str, doc: BaseModel) -> None: ...

    def get(self, tenant_id: str, collection: str, doc_id: str, model: type[T]) -> T | None: ...

    def list(self, tenant_id: str, collection: str, model: type[T]) -> list[T]: ...

    def delete(self, tenant_id: str, collection: str, doc_id: str) -> None: ...
