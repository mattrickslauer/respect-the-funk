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
from remixkit.domain.models import SectionRole
from remixkit.ports.audio import AnalyzedSection, AudioAnalysis
from remixkit.services.analysis import JOB_TYPE as ANALYSIS_JOB_TYPE, AnalysisService
from remixkit.services.errors import AnalysisUnavailable
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

    # Deleting is not enough for the fields that reach the *network*. `get_settings()`
    # reads `app/.env`, and `create_app()` now uses `ssm_path` from it to fetch secrets —
    # so a developer with a real `.env` would have the whole suite calling AWS, turning a
    # 3-second run into a 45-second one that fails on a plane. An environment variable
    # outranks the file, so setting these empty is what actually closes the file.
    for key in ("RK_SSM_PATH", "RK_GCP_PROJECT", "RK_AWS_PROFILE"):
        monkeypatch.setenv(key, "")


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
    for job_type in (JOB_TYPE, ANALYSIS_JOB_TYPE):
        built.queue.register(job_type, lambda payload: built.enqueued.append(payload))  # type: ignore[attr-defined]
    # Measurement is the one adapter with no zero-credential real implementation — numpy
    # is an optional extra — so tests drive a fake one explicitly rather than depending on
    # whether the machine running them happens to have it installed.
    built.analysis = AnalysisService(built.songs, built.storage, built.queue, FakeAnalyzer())
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


# ---------------------------------------------------------------- measurement
# The numbers below are `losing-sleep`'s, from content/RHYTHM-STUDY.md: 125 BPM (beat
# 480ms, bar 1920ms), the drop at 124784ms, a 2-bar riser and a 7-bar plateau. Using a
# real song's real measurement rather than round numbers means the fixture exercises the
# same awkward arithmetic the console will meet — 6.4-second hooks, not 6-second ones.
BEAT_MS = 480.0
DROP_MS = 124_784


def sample_analysis(**overrides) -> AudioAnalysis:
    fields = dict(
        method="fake: fixture measurement, not a real decode",
        duration_ms=214_000,
        bpm=125.0,
        beat_ms=BEAT_MS,
        downbeat_ms=DROP_MS,
        drop_ms=DROP_MS,
        riser_beats=8,
        plateau_beats=28,
        bar_phase_confidence=0.83,
        sections=[
            AnalyzedSection(
                role=SectionRole.INTRO, label="Intro 1", start_ms=0, end_ms=30_704,
                beats=64, energy_low_band=6.1, energy_rms_db=-12.4,
            ),
            AnalyzedSection(
                role=SectionRole.CHORUS, label="Chorus 1", start_ms=30_704, end_ms=61_424,
                beats=64, energy_low_band=54.2, energy_rms_db=-4.9,
            ),
            AnalyzedSection(
                role=SectionRole.BREAKDOWN, label="Breakdown 1", start_ms=61_424,
                end_ms=124_784, beats=132, energy_low_band=0.5, energy_rms_db=-12.3,
            ),
            AnalyzedSection(
                role=SectionRole.DROP, label="Drop 1", start_ms=DROP_MS, end_ms=138_224,
                beats=28, energy_low_band=58.8, energy_rms_db=-4.7,
            ),
        ],
    )
    fields.update(overrides)
    return AudioAnalysis(**fields)


class FakeAnalyzer:
    """An analyser under a test's control — never a plausible default.

    It returns whatever the test handed it, which is the opposite of the mock generator's
    job. There is no "mock analyser" in the app for the reason spelled out in
    `adapters/audio_unavailable.py`: a fabricated BPM is a float that nothing downstream
    could distinguish from a measured one.
    """

    name = "fake"

    def __init__(self, analysis: AudioAnalysis | None = None, *, available: bool = True,
                 raises: Exception | None = None) -> None:
        self._analysis = analysis
        self._raises = raises
        self.available = available
        self.unavailable_reason = "" if available else "numpy is not installed in this test"
        self.calls: list[dict] = []

    def analyze(self, data: bytes, *, filename: str = "", drop_ms: int | None = None):
        self.calls.append({"bytes": len(data), "filename": filename, "drop_ms": drop_ms})
        if not self.available:
            raise AnalysisUnavailable(self.unavailable_reason)
        if self._raises:
            raise self._raises
        return self._analysis or sample_analysis()


@pytest.fixture
def measured_song(container, principal):
    """An artist, a song, an uploaded master, and one analysis already applied."""
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, b"RIFF-not-really-audio", content_type="audio/wav")
    container.songs.register_master(principal, song.id, key=key, content_type="audio/wav")
    return container.analysis.run(principal.tenant_id, song.id)


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
