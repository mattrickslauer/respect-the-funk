"""Configuration, from the environment and nowhere else.

Secrets Manager is not used. One secret there is $0.40/month, which is real money
against an idle bill this project intends to keep at zero, and Lambda environment
variables are already encrypted at rest with a KMS key AWS provides free. Revisit
when there is a second consumer that needs rotation without a deploy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str
    tenant_slug: str

    @property
    def configured(self) -> bool:
        """False in a fresh checkout with no .env — the console says so rather
        than dying with a connection error nobody can act on."""
        return bool(self.database_url)


def load() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        # Writes require this. Empty means the console is read-only for everyone,
        # which is the correct posture for a deployment nobody has configured.
        admin_token=os.environ.get("PLATFORM_ADMIN_TOKEN", ""),
        tenant_slug=os.environ.get("PLATFORM_TENANT_SLUG", "respect-the-funk"),
    )
