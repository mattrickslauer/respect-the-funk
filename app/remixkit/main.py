"""The app.

One FastAPI process serves the console, the JSON API, and (on the inline queue) the
worker — which is BUILD-SPEC §2's "one deploy" decision. In the deployed shape the
same code runs in two places: Lambda serves the routes, and Batch runs `worker.py`,
which imports the same services and touches none of this module.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from genblaze_core.exceptions import StorageError

from remixkit.api.v1 import router as api_router
from remixkit.auth.provider import AuthError
from remixkit.bootstrap import export_for_libs, load_secrets
from remixkit.deps import get_container
from remixkit.services.errors import ServiceError
from remixkit.settings import get_settings
from remixkit.ui.routes import CONSOLE_PATH, LOGIN_PATH, router as ui_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("remixkit")

STATIC_DIR = Path(__file__).parent / "ui" / "static"


def _wants_html(request: Request) -> bool:
    """Is this a browser navigating, or a client calling the API?

    `Accept` rather than the path, because `/docs` and `/healthz` are outside `/api` and
    a `curl` against `/` should still get JSON rather than a login page it cannot use.
    Browsers send `text/html` at the front of the list; `httpx` and `curl` do not.
    """
    return "text/html" in request.headers.get("accept", "")


def create_app() -> FastAPI:
    # Two reads of `get_settings()`, deliberately.
    #
    # Secrets have to land in the environment before the settings this process will live
    # by are built, because `get_settings()` is lru_cached — whatever the environment
    # looks like at the first call is what it believes forever. But the *location* of
    # those secrets is itself a setting, and on a laptop it comes from `app/.env`, which
    # only `Settings` knows how to read.
    #
    # So: read once to find out where the secrets are, fetch them, then drop the cache so
    # the authoritative read sees what SSM just supplied. In a deployment `ssm_path`
    # comes from the environment and the second read is simply identical.
    if load_secrets(get_settings().ssm_path, profile=get_settings().aws_profile):
        get_settings.cache_clear()
    settings = get_settings()
    export_for_libs(settings)
    app = FastAPI(
        title="RemixKit",
        version="0.1.0",
        summary="The label's artist console — Genblaze fan-out, B2 provenance.",
        description=(
            "Register an artist, build their identity once, attach songs, generate "
            "content designed to be imitated, and verify what came out.\n\n"
            "Authentication is selected by `RK_AUTH_BACKEND`. With `otp`, sign in at "
            "`POST /api/v1/auth/request-code` then `POST /api/v1/auth/verify-code`, and "
            "send the returned token as `Authorization: Bearer <token>`. With `none` "
            "(the default) every caller is the default tenant and no header is needed."
        ),
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(api_router)
    app.include_router(ui_router)

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        """A refusal is not a 500. JSON clients get a body; htmx callers get the
        component the UI routes already render for them."""
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def _storage_error(request: Request, exc: StorageError):
        """Storage failing is a 503, and it says so in the vendor's own words.

        This handler exists because the alternative was tried in production: a B2 daily
        cap made every read 403, the layers below read 403 as "no such object", and the
        console reported that songs which were sitting in the bucket did not exist. The
        catalogue took the blame for the storage, and the message that would have ended
        the investigation in a minute — *"download bandwidth or transaction (Class B) cap
        exceeded"* — was thrown away three frames down.

        503 rather than 500 because nothing is broken: the bucket is refusing to answer
        right now, and that is a condition with a clock on it. `str(exc)` carries B2's own
        sentence, which is the only part of this anybody can act on.
        """
        log.warning("storage unavailable (%s): %s", exc.error_code, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": f"Storage is not answering — {exc}"},
        )

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError) -> Response:
        """One raise, three correct answers — chosen by who is asking.

        A person in a browser wants the login page, with the URL they were after
        preserved so the sign-in lands them where they meant to go. An htmx fragment
        request cannot be answered with a redirect (htmx would swap the login page into
        a table cell), so it gets `HX-Redirect` and the browser navigates. A script
        wants a 401 with a body it can read.

        Doing this in one handler is why no route in the app has an auth branch in it.
        """
        if request.headers.get("hx-request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": LOGIN_PATH})
        if _wants_html(request):
            target = request.url.path
            if request.url.query:
                target = f"{target}?{request.url.query}"
            # The console root is where sign-in lands by default, so carrying it as
            # `?next=` would only put a redundant parameter in the URL bar. `/` never
            # reaches here at all — it is the public landing page.
            suffix = "" if target == CONSOLE_PATH else f"?next={quote(target, safe='')}"
            return RedirectResponse(f"{LOGIN_PATH}{suffix}", status_code=303)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/healthz", tags=["ops"])
    def healthz():
        """What this process is, including whether generation is mocked."""
        container = get_container()
        return {"ok": True, **container.describe()}

    @app.on_event("startup")
    def _announce() -> None:
        described = get_container().describe()
        log = logging.getLogger("remixkit")
        log.info("RemixKit up — %s", described)
        for warning in described["warnings"]:
            log.warning(warning)

    return app


app = create_app()
