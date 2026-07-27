"""No authentication. Every caller is the configured dev tenant.

This is the current, intended behaviour — not a stub that fails closed later. It is a
complete implementation of `AuthProvider` that happens to admit everyone, which keeps
the console usable with no login while the tenant axis stays real underneath.

The one guard worth its line: `settings.require_auth` refuses to let this provider be
the one serving a deployment that has declared itself authenticated. Shipping "no auth"
is a decision; shipping it *by accident* is an incident.
"""

from __future__ import annotations

from remixkit.auth.provider import AuthProvider, Principal


class AnonymousAuth(AuthProvider):
    name = "anonymous"

    def __init__(self, tenant_id: str, display_name: str = "Respect the Funk") -> None:
        self._principal = Principal(
            tenant_id=tenant_id,
            subject="anonymous",
            display_name=display_name,
            authenticated=False,
        )

    async def authenticate(self, *, authorization: str | None, cookies: dict[str, str]) -> Principal:
        return self._principal
