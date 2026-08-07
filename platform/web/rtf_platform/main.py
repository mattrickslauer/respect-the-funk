"""The ASGI app.

Runs identically under `uvicorn` locally and under Mangum in Lambda — see
`handler.py`. Nothing in the app knows which one it is, which is what keeps
"works on my machine" and "works in the deployment" the same statement.
"""

from __future__ import annotations

from fastapi import FastAPI

from rtf_platform.routes import router

app = FastAPI(
    title="Respect the Funk — platform console",
    docs_url=None,      # no interactive docs on a public URL
    redoc_url=None,
)
app.include_router(router)
