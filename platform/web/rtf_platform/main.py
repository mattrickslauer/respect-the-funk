"""The ASGI app.

Runs identically under `uvicorn` locally and under Mangum in Lambda — see
`handler.py`. Nothing in the app knows which one it is, which is what keeps
"works on my machine" and "works in the deployment" the same statement.
"""

from __future__ import annotations

from fastapi import FastAPI

from rtf_platform import console_assets
from rtf_platform.api import api
from rtf_platform.routes import router

app = FastAPI(
    title="Respect the Funk — platform console",
    docs_url=None,      # no interactive docs on a public URL
    redoc_url=None,
)
app.include_router(router)

# The JSON API, as its own application rather than as more routes on this one. Mounted
# because the two have different HTTP contracts and neither should leak into the other:
# an unauthenticated browser gets the console's 303 to a page that explains what this
# is, and an unauthenticated API client gets a 401 with a code it can branch on. See
# `rtf_platform/api/__init__.py` for the rest of the argument.
app.mount("/api", api)

# The React console's compiled assets, behind `/console`. Mounted unconditionally:
# when there is no build, the address serves a page saying so and giving the command,
# rather than a 404 an operator would read as a missing route. `console_assets` has
# the argument, and the reason a file request must never fall through to the
# application shell.
console_assets.mount(app)
