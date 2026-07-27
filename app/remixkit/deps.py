"""The composition root — every adapter choice in the codebase is made here, once.

This is the file to read to understand the deployment, and the only file to edit to
change one. Nothing else imports a concrete adapter: services take ports, routes take
services. So "run on a laptop with no credentials" and "run on Lambda + SQS + Batch
against B2 and GMI Cloud" differ by four environment variables and nothing else.

Auth followed exactly that shape: `auth/otp.py` plus a branch in `_build_auth`. No route
changed, because routes already depend on `current_principal` rather than assuming who
is calling.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, Header, Request

from remixkit.adapters.generator_genblaze import GenblazeGenerator
from remixkit.adapters.mailer_console import ConsoleMailer
from remixkit.adapters.queue_inline import InlineQueue
from remixkit.adapters.repo_documents import DocumentRepo
from remixkit.adapters.storage_local import LocalStorage
from remixkit.auth.anonymous import AnonymousAuth
from remixkit.auth.otp import OtpAuth
from remixkit.auth.provider import AuthError, AuthProvider, Principal
from remixkit.domain.models import Modality
from remixkit.ports.mailer import Mailer
from remixkit.services.accounts import AccountService
from remixkit.services.artists import ArtistService
from remixkit.services.catalogue import CatalogueService
from remixkit.services.delivery import DeliveryService
from remixkit.services.identities import IdentityService
from remixkit.services.kits import JOB_TYPE, KitService
from remixkit.services.songs import SongService
from remixkit.services.verify import VerifyService
from remixkit.settings import Settings, get_settings

log = logging.getLogger(__name__)


class Container:
    """Built once per process. Holds one instance of each adapter and service."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.storage = _build_storage(settings)
        self.repo = DocumentRepo(self.storage, prefix=settings.key_prefix)
        self.queue = _build_queue(settings)
        self.mailer: Mailer = _build_mailer(settings)

        # Accounts is built before auth because the OTP provider reads sessions through
        # it — one object owns the signing key, so a token minted at sign-in and a token
        # verified on the next request cannot disagree about the secret.
        self.accounts = AccountService(
            self.repo,
            self.mailer,
            tenant_id=settings.default_tenant_id,
            allowlist=settings.allowlist,
            session_secret=_session_secret(settings),
            session_ttl_s=settings.session_ttl_s,
            otp_length=settings.otp_length,
            otp_ttl_s=settings.otp_ttl_s,
            otp_max_attempts=settings.otp_max_attempts,
            otp_resend_interval_s=settings.otp_resend_interval_s,
            # Only a dev process with no mailer ever shows a code back to its caller.
            reveal_codes=settings.env == "dev",
            app_name=settings.app_name,
        )
        self.auth: AuthProvider = _build_auth(settings, self.accounts)
        self.generator = GenblazeGenerator(
            self.storage,
            backend=settings.generator_backend,
            prefix=settings.key_prefix,
            models={
                Modality.VIDEO: settings.video_model,
                Modality.IMAGE: settings.image_model,
                Modality.AUDIO: settings.audio_model,
            },
            timeout_s=settings.generation_timeout_s,
            max_concurrency=settings.max_concurrency,
        )

        self.artists = ArtistService(self.repo)
        self.identities = IdentityService(self.repo)
        self.songs = SongService(self.repo, self.storage, key_prefix=settings.key_prefix)
        self.kits = KitService(
            self.repo,
            self.queue,
            self.generator,
            self.artists,
            self.identities,
            self.songs,
            max_shots=settings.max_shots_per_kit,
        )
        self.delivery = DeliveryService(self.storage, self.kits)
        self.catalogue = CatalogueService(artists=self.artists, songs=self.songs, kits=self.kits)
        self.verify = VerifyService()

        # The inline queue needs the handler that the SQS path gives to the worker
        # container. Same callable, different trigger — that symmetry is the point.
        if isinstance(self.queue, InlineQueue):
            self.queue.register(
                JOB_TYPE,
                lambda payload: self.kits.run(payload["tenant_id"], payload["kit_id"]),
            )

    def describe(self) -> dict[str, Any]:
        """What this process actually is — surfaced on /healthz and in the UI footer.

        A demo running on mock providers that looks identical to one running on GMI
        Cloud is a way to mislead a judge by accident. This makes the difference
        impossible to miss.
        """
        warnings: list[str] = []
        if self.settings.generator_backend == "mock":
            warnings.append("Generation is MOCKED — assets are synthetic, no provider is called.")
        if self.settings.storage_backend == "local":
            warnings.append("Storage is a local directory, not B2.")
        if getattr(self.storage, "ephemeral", False):
            warnings.append("Storage is ephemeral (/tmp) — data will not survive a restart.")
        if self.settings.auth_backend == "none":
            warnings.append("No authentication — every caller is the default tenant.")
        elif not self.settings.allowlist:
            warnings.append("Auth is on but RK_ALLOWED_EMAILS is empty — nobody can sign in.")
        if self.settings.auth_backend != "none" and not self.settings.session_secret:
            warnings.append(
                "RK_SESSION_SECRET is unset — sessions are signed with a per-process key "
                "and everyone is signed out on restart."
            )
        if self.settings.mail_backend == "console":
            if self.settings.auth_backend != "none" and self.settings.env != "dev":
                warnings.append(
                    "Auth is on but mail is NOT configured — codes go to the server log "
                    "only, so nobody outside the log can sign in."
                )
            else:
                warnings.append("Mail is not configured — sign-in codes go to the server log.")
        if self.settings.queue_backend == "sqs" and not (
            self.settings.batch_job_queue and self.settings.batch_job_definition
        ):
            warnings.append(
                "No Batch job queue configured — kits will sit on SQS with nothing to run them."
            )
        if not getattr(self.queue, "can_execute", True):
            warnings.append(getattr(self.queue, "unavailable_reason", "The job queue cannot execute jobs."))
        return {
            "env": self.settings.env,
            "tenant": self.settings.default_tenant_id,
            "auth": self.auth.name,
            "storage": getattr(self.storage, "name", "unknown"),
            "generator": self.generator.name,
            "queue": self.queue.name,
            "mail": self.mailer.name,
            "warnings": warnings,
        }


# ---------------------------------------------------------------- adapter builders
def _session_secret(settings: Settings) -> str:
    """The configured secret, or a fresh random one per process.

    Falling back to random rather than to a constant is the point: a hardcoded default
    signing key that nobody remembers to override is a forged-session bug waiting in
    every deployment that copied the example config. A random one is merely annoying —
    sessions do not survive a restart, and on Lambda they do not survive between
    instances, which is loud enough that it gets configured before anyone relies on it.
    `require_auth` turns that annoyance into a refusal to start.
    """
    if settings.session_secret:
        return settings.session_secret
    if settings.require_auth:
        raise RuntimeError(
            "RK_REQUIRE_AUTH is set but RK_SESSION_SECRET is empty. Refusing to sign "
            "sessions with a key that changes on every restart."
        )
    return secrets.token_urlsafe(32)


def _build_auth(settings: Settings, accounts: AccountService) -> AuthProvider:
    if settings.require_auth and settings.auth_backend == "none":
        raise RuntimeError(
            "RK_REQUIRE_AUTH is set but RK_AUTH_BACKEND=none. Refusing to start an "
            "unauthenticated deployment that believes it is authenticated."
        )
    if settings.auth_backend == "otp":
        if not settings.allowlist:
            # An empty allowlist is not "open" — it is a console nobody can enter. Say
            # so at startup rather than after someone types their address and waits.
            log.warning("auth_backend=otp with an empty RK_ALLOWED_EMAILS — nobody can sign in")
        return OtpAuth(accounts, cookie_name=settings.session_cookie)
    return AnonymousAuth(settings.default_tenant_id, settings.default_tenant_name)


def _build_mailer(settings: Settings) -> Mailer:
    if settings.mail_backend == "zeptomail":
        from remixkit.adapters.mailer_zepto import ZeptoMailer

        return ZeptoMailer(
            token=settings.zeptomail_token,
            sender=settings.mail_from,
            sender_name=settings.mail_from_name,
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
        )
    return ConsoleMailer()


def _build_storage(settings: Settings):
    if settings.storage_backend == "b2":
        from remixkit.adapters.storage_b2 import B2Storage

        if not settings.b2_bucket:
            raise RuntimeError("storage_backend=b2 requires RK_B2_BUCKET")
        return B2Storage(
            settings.b2_bucket,
            key_id=settings.b2_key_id,
            app_key=settings.b2_app_key,
            region=settings.b2_region,
        )
    return LocalStorage(settings.local_storage_dir)


def _build_queue(settings: Settings):
    if settings.queue_backend == "sqs":
        from remixkit.adapters.queue_sqs import SQSQueue

        return SQSQueue(
            settings.sqs_queue_url,
            region=settings.aws_region,
            batch_job_queue=settings.batch_job_queue,
            batch_job_definition=settings.batch_job_definition,
        )
    return InlineQueue()


@lru_cache
def get_container() -> Container:
    return Container(get_settings())


# ---------------------------------------------------------------- FastAPI wiring
async def current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Every route depends on this, including today's routes that need nothing.

    That is the whole cost of being auth-ready, and it is paid here rather than in a
    later sweep across every handler.
    """
    container = get_container()
    return await container.auth.authenticate(
        authorization=authorization, cookies=dict(request.cookies)
    )


async def optional_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal | None:
    """`current_principal` that returns None instead of raising.

    Exactly two routes want this — the login page (which must render for someone with no
    session, but should bounce someone who already has one straight to the roster) and
    the layout's account menu. Everything else wants the strict version, so this is a
    separate dependency rather than a flag: a handler cannot opt out of auth by
    accident, only by naming the lenient one.
    """
    container = get_container()
    try:
        return await container.auth.authenticate(
            authorization=authorization, cookies=dict(request.cookies)
        )
    except AuthError:
        return None


def get_accounts() -> AccountService:
    return get_container().accounts


def get_artists() -> ArtistService:
    return get_container().artists


def get_identities() -> IdentityService:
    return get_container().identities


def get_songs() -> SongService:
    return get_container().songs


def get_kits() -> KitService:
    return get_container().kits


def get_verify() -> VerifyService:
    return get_container().verify


def get_catalogue() -> CatalogueService:
    return get_container().catalogue


def get_delivery() -> DeliveryService:
    return get_container().delivery


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
MaybePrincipal = Annotated[Principal | None, Depends(optional_principal)]
Accounts = Annotated[AccountService, Depends(get_accounts)]
Artists = Annotated[ArtistService, Depends(get_artists)]
Identities = Annotated[IdentityService, Depends(get_identities)]
Songs = Annotated[SongService, Depends(get_songs)]
Kits = Annotated[KitService, Depends(get_kits)]
Verify = Annotated[VerifyService, Depends(get_verify)]
Catalogue = Annotated[CatalogueService, Depends(get_catalogue)]
Delivery = Annotated[DeliveryService, Depends(get_delivery)]
