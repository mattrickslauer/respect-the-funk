"""Configuration — one object, env-driven, with defaults that run on a laptop.

The four `*_backend` fields are the whole deployment story. Each selects an adapter
behind a port; the defaults are the zero-credential path, and every production value
is additive rather than a rewrite:

    storage_backend    local  → b2
    generator_backend  mock   → genblaze
    queue_backend      inline → sqs
    auth_backend       none   → (oidc, when it exists)

`RK_` prefixes everything so these never collide with the provider keys Genblaze reads
directly from the environment (`GMI_API_KEY`, `B2_KEY_ID`, …).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RK_", env_file=".env", extra="ignore")

    app_name: str = "RemixKit"
    env: Literal["dev", "staging", "prod"] = "dev"

    # ---- tenancy ---------------------------------------------------------------
    # The label. Tenant #1 dogfoods everything (PRODUCT.md); the product is generic.
    default_tenant_id: str = "respect-the-funk"
    default_tenant_name: str = "Respect the Funk"

    # ---- adapter selection -----------------------------------------------------
    storage_backend: Literal["local", "b2"] = "local"
    generator_backend: Literal["mock", "genblaze"] = "mock"
    queue_backend: Literal["inline", "sqs"] = "inline"
    auth_backend: Literal["none"] = "none"

    # A deployment that declares itself authenticated must not be served by
    # AnonymousAuth. Startup fails loudly rather than quietly admitting everyone.
    require_auth: bool = False

    # ---- storage ---------------------------------------------------------------
    local_storage_dir: Path = Path(".remixkit-data")
    b2_bucket: str = ""
    b2_key_id: str = ""
    b2_app_key: str = ""
    b2_region: str = ""
    key_prefix: str = "remixkit"

    # ---- generation ------------------------------------------------------------
    video_model: str = "seedance-2-0-260128"
    image_model: str = "seedream-5.0-lite"
    audio_model: str = "tts"
    generation_timeout_s: float = 900.0
    max_concurrency: int = 4
    # Kits are capped because provider latency and quota are the named Layer-1 risk
    # (BUILD-SPEC §12). Small kits fail fast and cost little.
    max_shots_per_kit: int = 8

    # ---- queue -----------------------------------------------------------------
    sqs_queue_url: str = ""
    aws_region: str = "us-east-1"

    @property
    def is_readonly_fs(self) -> bool:
        """Serverless filesystems are read-only apart from /tmp. The local storage
        adapter needs to know so it can relocate rather than crash on first write."""
        return self.env != "dev" and self.storage_backend == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
