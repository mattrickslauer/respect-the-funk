"""The app.

One FastAPI process serves the console, the JSON API, and (on the inline queue) the
worker — which is BUILD-SPEC §2's "one deploy" decision. In the deployed shape the
same code runs in two places: Lambda serves the routes, and Batch runs `worker.py`,
which imports the same services and touches none of this module.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from remixkit.api.v1 import router as api_router
from remixkit.bootstrap import load_secrets
from remixkit.deps import get_container
from remixkit.services.errors import ServiceError
from remixkit.settings import get_settings
from remixkit.ui.routes import router as ui_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "ui" / "static"


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
            "**There is no authentication.** Every caller is the default tenant. See "
            "`remixkit/auth/` for the seam this plugs into."
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
