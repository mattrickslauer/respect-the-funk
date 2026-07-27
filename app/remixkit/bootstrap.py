"""Load secrets from SSM into the environment, before settings are read.

Terraform creates the parameters but deliberately never holds their values — putting a
B2 application key into Terraform state would store it in plaintext. So the values are
set out of band, and something has to fetch them at runtime. This is that something.

It must run **before** the first `get_settings()` call, because that is `lru_cache`d and
whatever the environment looked like at that moment is what the process believes
forever. Hence a plain function called at the top of `create_app()` and the worker's
`main()`, rather than a FastAPI dependency or a startup hook.

Names map straight across: the parameter `/remixkit/prod/B2_APP_KEY` becomes the
environment variable `RK_B2_APP_KEY`, so a value that arrives via SSM in production and
via `.env` on a laptop is read by the same settings field either way. Provider keys
that Genblaze reads directly from the environment (`GMI_API_KEY`, `ELEVENLABS_API_KEY`)
are additionally exported unprefixed, because those are read by the library, not by us.

Failure is fatal on purpose. A process that cannot read its credentials should not come
up and serve errors; it should refuse to start, where a deployment notices.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Keys Genblaze and its provider extras read directly from the environment. These get
# exported without the RK_ prefix as well as with it.
_PROVIDER_KEYS = {
    "GMI_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_API_KEY",
    "B2_KEY_ID",
    "B2_APP_KEY",
}


def load_secrets(path: str | None = None, region: str | None = None) -> int:
    """Fetch every parameter under `path` into `os.environ`. Returns how many landed.

    A no-op when `RK_SSM_PATH` is unset, which is the whole local-development story:
    nothing to configure, nothing to stub.
    """
    path = path or os.environ.get("RK_SSM_PATH", "")
    if not path:
        return 0

    region = region or os.environ.get("RK_AWS_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise RuntimeError(
            "RK_SSM_PATH is set but boto3 is not installed: pip install 'remixkit[queue]'"
        ) from exc

    client = boto3.client("ssm", region_name=region)
    loaded = 0

    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=path, Recursive=True, WithDecryption=True):
        for parameter in page.get("Parameters", []):
            name = parameter["Name"].rsplit("/", 1)[-1]
            value = parameter.get("Value", "")

            # Terraform seeds every parameter with a placeholder. Treating that as a
            # real credential would produce a confusing auth failure from the provider
            # instead of an obvious configuration error here.
            if not value or value.startswith("PLACEHOLDER"):
                log.warning("SSM parameter %s is unset — skipping", parameter["Name"])
                continue

            os.environ.setdefault(f"RK_{name}", value)
            if name in _PROVIDER_KEYS:
                os.environ.setdefault(name, value)
            loaded += 1

    log.info("loaded %d secret(s) from SSM path %s", loaded, path)
    return loaded
