#!/usr/bin/env python3
"""Vertex AI auth that still works.

The generation tools were written against **express mode**: an `AGENT_GCP_KEY` sent as
`x-goog-api-key` to `aiplatform.googleapis.com/v1/publishers/google/models/...`. That path
is now dead in both directions —

    aiplatform.googleapis.com  + api key  -> 401 CREDENTIALS_MISSING
                                             "API keys are not supported by this API"
    the same host with any other model    -> 404

— so `generate_stills.py`, `generate_hook.py` and `check_likeness.py` all fail before they
spend a cent, which is at least the right failure. The credentials to fix it were already
on this machine: an application-default login, plus `GCP_PROJECT` / `GCP_LOCATION` in
`.env` that express mode never used.

The fix is the *project-scoped regional* endpoint with an OAuth bearer token:

    https://{loc}-aiplatform.googleapis.com/v1/projects/{proj}/locations/{loc}
        /publishers/google/models/{model}:generateContent

Verified working on 2026-07-24 for `gemini-2.5-flash` (text) and `gemini-2.5-flash-image`
(image, ~1.1MB PNG returned). `gemini-2.0-flash` and `gemini-3-pro-image-preview` 404 on
this project, so do not assume a model exists because it exists elsewhere.

Tokens are cached and refreshed on demand; `available()` is false when ADC or the project
is missing, so callers can fall back to the old key path rather than crash.
"""

from __future__ import annotations

import os
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_CREDS = None


def load_dotenv(start: Path) -> None:
    """Read the nearest .env walking up. Does not overwrite real env vars."""
    for d in [start, *start.parents]:
        f = d / ".env"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
        return


def project() -> str | None:
    return os.environ.get("GCP_PROJECT")


def location() -> str:
    return os.environ.get("GCP_LOCATION", "us-central1")


def _creds():
    global _CREDS
    if _CREDS is None:
        import google.auth
        _CREDS, _ = google.auth.default(scopes=[SCOPE])
    return _CREDS


def token() -> str:
    import google.auth.transport.requests
    c = _creds()
    if not c.valid:
        c.refresh(google.auth.transport.requests.Request())
    return c.token


def available() -> bool:
    if not project():
        return False
    try:
        token()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def endpoint(model: str, method: str = "generateContent") -> str:
    loc, proj = location(), project()
    return (f"https://{loc}-aiplatform.googleapis.com/v1/projects/{proj}"
            f"/locations/{loc}/publishers/google/models/{model}:{method}")


def headers() -> dict:
    return {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
