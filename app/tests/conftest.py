"""Test fixtures.

Each test gets its own storage directory and its own container, so nothing leaks
between tests through the module-level `lru_cache` on `get_container`.

Nothing leaks in from the *developer* either — see `_hermetic_env`. A `Settings()` built
with explicit keyword arguments still merges `app/.env` and `RK_*` from the environment
for every field the test did not name, so without that fixture the suite's behaviour
depends on whether the person running it happens to have auth turned on locally.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from remixkit import deps
from remixkit.services.kits import JOB_TYPE
from remixkit.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Strip ambient RemixKit configuration before any test builds `Settings`.

    Autouse and unconditional: a test that reads the developer's `.env` passes or fails
    for reasons that are invisible in the diff and absent from CI, which is the worst
    shape a failure can have. Fields a test does not name now take their declared
    defaults, everywhere.
    """
    for key in [k for k in os.environ if k.startswith("RK_")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        # `_env_file=None` is the other half of `_hermetic_env`: it closes the file, as
        # that closes the environment.
        _env_file=None,
        env="dev",
        local_storage_dir=tmp_path / "data",
        storage_backend="local",
        generator_backend="mock",
        queue_backend="inline",
        default_tenant_id="test-label",
    )


@pytest.fixture
def container(settings, monkeypatch) -> deps.Container:
    """A container whose queue *records* instead of executing.

    The inline queue runs jobs on a background thread, which is correct behaviour and
    terrible determinism: a test that calls `kits.run()` directly would race the thread
    already running the same kit. So tests drive the worker explicitly, and the one
    test that cares about the thread uses `inline_container` below.
    """
    built = deps.Container(settings)
    built.enqueued = []  # type: ignore[attr-defined]
    built.queue._handlers.clear()
    built.queue.register(JOB_TYPE, lambda payload: built.enqueued.append(payload))  # type: ignore[attr-defined]
    monkeypatch.setattr(deps, "get_container", lambda: built)
    get_settings.cache_clear()
    return built


@pytest.fixture
def inline_container(settings, monkeypatch) -> deps.Container:
    """The real thing, background thread and all."""
    built = deps.Container(settings)
    monkeypatch.setattr(deps, "get_container", lambda: built)
    get_settings.cache_clear()
    return built


@pytest.fixture
def principal(container):
    return container.auth._principal  # the anonymous dev principal


@pytest.fixture
def client(container, monkeypatch) -> TestClient:
    # main and the routers captured `get_container` at import time via the module,
    # so patch it there too rather than relying on import order.
    import remixkit.api.v1 as api_v1
    import remixkit.main as main_mod
    import remixkit.ui.routes as ui_routes

    for module in (api_v1, main_mod, ui_routes):
        monkeypatch.setattr(module, "get_container", lambda: container, raising=False)

    monkeypatch.setattr(deps, "get_container", lambda: container)
    return TestClient(main_mod.create_app())
