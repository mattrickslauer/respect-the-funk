"""Test fixtures.

Each test gets its own storage directory and its own container, so nothing leaks
between tests through the module-level `lru_cache` on `get_container`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remixkit import deps
from remixkit.services.kits import JOB_TYPE
from remixkit.settings import Settings, get_settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
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
