"""Configuration — one object, env-driven, with defaults that run on a laptop.

The four `*_backend` fields are the whole deployment story. Each selects an adapter
behind a port; the defaults are the zero-credential path, and every production value
is additive rather than a rewrite:

    storage_backend    local  → b2
    generator_backend  mock   → genblaze
    queue_backend      inline → sqs
    auth_backend       none   → otp
    mail_backend       console → zeptomail

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
    auth_backend: Literal["none", "otp"] = "none"
    mail_backend: Literal["console", "zeptomail"] = "console"

    # A deployment that declares itself authenticated must not be served by
    # AnonymousAuth. Startup fails loudly rather than quietly admitting everyone.
    require_auth: bool = False

    # ---- auth: email OTP -------------------------------------------------------
    # Who may sign in, comma-separated. This IS the user table — an allowlist rather
    # than open registration, because the console administers a real label's roster and
    # "anyone with an email address" is not the intended population. Growing past a
    # handful of people is the signal to make accounts self-registering behind an
    # invite, not to keep extending this string.
    allowed_emails: str = ""

    # Signs session tokens and hashes OTP codes. Empty is the laptop default: a random
    # per-process secret is generated, which logs everyone out on restart — correct for
    # dev, and `require_auth` refuses to let it happen in a deployment.
    session_secret: str = ""
    session_ttl_s: int = 7 * 24 * 3600
    session_cookie: str = "rk_session"
    # Cookies are Secure everywhere except dev, where there is no TLS on localhost.
    session_cookie_secure: bool = True

    otp_length: int = 6
    otp_ttl_s: int = 600
    # Five guesses against a six-digit code over ten minutes. Past this the challenge is
    # destroyed and the user must request a new one — which is also the rate limit on
    # brute force, since each new challenge is a new code.
    otp_max_attempts: int = 5
    # Floor between two code requests for the same address, so the sign-in form cannot
    # be used to mailbomb someone on the allowlist.
    otp_resend_interval_s: int = 30

    # ---- mail ------------------------------------------------------------------
    # ZeptoMail's SMTP password and its "send mail token" are the same secret.
    zeptomail_token: str = ""
    mail_from: str = "rtp@agfarms.dev"
    mail_from_name: str = "Respect the Funk"
    smtp_host: str = "smtp.zeptomail.com"
    smtp_port: int = 465
    smtp_user: str = "emailapikey"

    @property
    def allowlist(self) -> frozenset[str]:
        """The allowlist, normalised. Comma-separated rather than a JSON list because
        this arrives as one SSM parameter and one `.env` line, and both are edited by
        hand."""
        return frozenset(
            part.strip().lower() for part in self.allowed_emails.split(",") if part.strip()
        )

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

    # ---- secrets ---------------------------------------------------------------
    # Where `bootstrap.load_secrets` fetches credentials from. Declared here rather than
    # read only from the process environment so that `app/.env` can name it: an .env
    # that turns on `storage_backend=b2` but cannot say where the B2 keys live sets up a
    # failure whose message is about missing credentials rather than about the missing
    # setting that would have found them.
    ssm_path: str = ""
    # Vertex project/region for the Google (Veo/Imagen) provider. Settings rather than
    # bare environment variables so `app/.env` can supply them — see `export_for_libs`.
    gcp_project: str = ""
    gcp_location: str = "us-central1"
    # Which AWS credential profile to use. Empty means boto3's own resolution (env vars,
    # then `default`), which is right on Lambda — the execution role has no profile.
    aws_profile: str = ""

    # ---- queue -----------------------------------------------------------------
    sqs_queue_url: str = ""
    aws_region: str = "us-east-1"
    # Which Batch job to start when a kit is enqueued. Without these the message lands
    # on SQS and nothing consumes it.
    batch_job_queue: str = ""
    batch_job_definition: str = ""

    @property
    def is_readonly_fs(self) -> bool:
        """Serverless filesystems are read-only apart from /tmp. The local storage
        adapter needs to know so it can relocate rather than crash on first write."""
        return self.env != "dev" and self.storage_backend == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
