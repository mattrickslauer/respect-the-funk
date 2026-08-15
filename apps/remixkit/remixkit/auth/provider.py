"""The auth contract: what a caller *is*, and what may reject them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AuthError(Exception):
    """Raised by a provider that declines a request. `AnonymousAuth` never raises it."""

    def __init__(self, detail: str = "Not authenticated", status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    """Who is asking, and on whose behalf.

    `tenant_id` is the load-bearing field — it becomes the partition key on every
    document, every object key, and every job payload. Today it is a constant. The
    day it comes off a verified token instead, nothing downstream notices.
    """

    tenant_id: str
    subject: str = "anonymous"
    display_name: str = "Anonymous"
    scopes: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False

    def can(self, scope: str) -> bool:
        """Scope check. Open by default while unauthenticated — this is the single
        place that becomes a real deny once a provider is wired in, and the call sites
        in `services/` are already written against it."""
        if not self.authenticated:
            return True
        return scope in self.scopes


class AuthProvider(Protocol):
    name: str

    async def authenticate(self, *, authorization: str | None, cookies: dict[str, str]) -> Principal:
        """Resolve a principal, or raise `AuthError`."""
