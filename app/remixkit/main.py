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

from remixkit.api.v1 import router as api_router
from remixkit.auth.provider import AuthError
from remixkit.bootstrap import load_secrets
from remixkit.deps import get_container
from remixkit.services.errors import ServiceError
from remixkit.settings import get_settings
from remixkit.ui.routes import CONSOLE_PATH, LOGIN_PATH, router as ui_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "ui" / "static"


def _wants_html(request: Request) -> bool:
    """Is this a browser navigating, or a client calling the API?

    `Accept` rather than the path, because `/docs` and `/healthz` are outside `/api` and
    a `curl` against `/` should still get JSON rather than a login page it cannot use.
    Browsers send `text/html` at the front of the list; `httpx` and `curl` do not.
    """
    return "text/html" in request.headers.get("accept", "")


def create_app() -> FastAPI:
    # Before `get_settings()` — it is lru_cached, so the environment as it stands at
    # the first call is what this process believes for its whole life.
    load_secrets()
    settings = get_settings()
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
