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
    masters_bucket: str
    region: str

    @property
    def configured(self) -> bool:
        """False in a fresh checkout with no .env — the console says so rather
        than dying with a connection error nobody can act on."""
        return bool(self.database_url)

    @property
    def storage_configured(self) -> bool:
        """Whether masters can be uploaded at all.

        Deliberately separate from `configured`: storage is the one capability here
        that can be absent while everything else works, and the console needs to say
        which of the two states it is in rather than rendering an upload form whose
        submit will raise. `storage.py`'s header explains why there is no local
        adapter to fall back to.
        """
        return bool(self.masters_bucket)


def load() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        # Writes require this. Empty means the console is read-only for everyone,
        # which is the correct posture for a deployment nobody has configured.
        admin_token=os.environ.get("PLATFORM_ADMIN_TOKEN", ""),
        tenant_slug=os.environ.get("PLATFORM_TENANT_SLUG", "respect-the-funk"),
        # Where masters go. Empty is a legitimate state — a checkout that has never
        # run `terraform apply` has no bucket — and the console reports it rather
        # than inventing a local directory nothing else in the system can read.
        masters_bucket=os.environ.get("PLATFORM_MASTERS_BUCKET", ""),
        # Only used to construct the S3 client. `AWS_REGION` is set by the Lambda
        # runtime itself, so in the deployed function this needs no configuration;
        # the explicit variable is for a worker or a laptop, and the default matches
        # `platform/infra/variables.tf`.
        region=(os.environ.get("PLATFORM_REGION")
                or os.environ.get("AWS_REGION")
                or "us-east-1"),
    )
