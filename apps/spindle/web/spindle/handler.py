"""Lambda entry point.

Mangum translates a Lambda Function URL event into an ASGI scope. `lifespan="off"`
because there is no startup work to do and a lifespan protocol on a function that
may be frozen mid-request is a source of confusing timeouts.
"""

from __future__ import annotations

from mangum import Mangum

from spindle.main import app

handler = Mangum(app, lifespan="off")
