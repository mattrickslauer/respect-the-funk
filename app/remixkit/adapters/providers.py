"""Provider resolution — the only thing that differs between the mock and live paths.

Kept separate from the generator so that "run with no credentials" and "run against
GMI Cloud" are the *same pipeline code* with a different provider table. If the mock
path had its own generator, the thing we test on a laptop would not be the thing that
runs in Batch, and the manifest we verify in dev would prove nothing about the one in
B2.

Live providers are imported lazily and individually: `genblaze-elevenlabs` missing must
not stop video and image from working, because a partial provider set is the normal
state of a hackathon build.
"""

from __future__ import annotations

import logging
from typing import Any

from remixkit.domain.models import Modality

log = logging.getLogger(__name__)

# Illustrative unit costs so the ledger has something honest to show on the mock path.
# Real per-call cost comes off `step.cost_usd` when a live provider reports it.
MOCK_COST_USD: dict[Modality, float] = {
    Modality.VIDEO: 0.42,
    Modality.IMAGE: 0.03,
    Modality.AUDIO: 0.02,
}


def _asset_factory(modality: Modality):
    """Build the `assets=` callable Genblaze's MockProvider takes.

    It receives the `Step` and must return real assets. We synthesise a file and hand
    back a `file://` URL so the sink's transfer, hashing, and manifest writing all run
    for real — the only synthetic thing in the run is the pixels.
    """
    from genblaze_core.models.asset import Asset

    from remixkit.adapters import mock_media

    media_types = {
        Modality.VIDEO: "video/mp4",
        Modality.IMAGE: "image/png",
        Modality.AUDIO: "audio/mpeg",
    }

    def make(step: Any) -> list[Asset]:
        prompt = step.prompt or "untitled"
        params = dict(getattr(step, "params", None) or {})
        seconds = float(params.get("duration") or 6.0)

        if modality is Modality.VIDEO:
            path = mock_media.video(prompt, seconds)
        elif modality is Modality.IMAGE:
            path = mock_media.image(prompt)
        else:
            path = mock_media.audio(prompt, min(seconds, 5.0))

        if path is None:
            raise RuntimeError(f"Could not synthesise mock {modality.value} (is ffmpeg installed?)")

        return [
            Asset(
                url=path.resolve().as_uri(),
                media_type=media_types[modality],
                size_bytes=path.stat().st_size,
            )
        ]

    return make


def mock_providers() -> dict[Modality, Any]:
    """Genblaze's own mocks, fed real files.

    Real `Pipeline`, real `ObjectStorageSink`, real manifest, real content hashes —
    synthetic pixels. That combination is what makes the laptop run meaningful: the
    manifest verified in dev is produced by the same code that produces the one in B2.

    Modalities whose media cannot be synthesised (no ffmpeg) are left out entirely, so
    they surface through the same "no provider for this modality" path as a missing
    provider extra rather than failing mid-run.
    """
    from genblaze_core.mocks import MockAudioProvider, MockProvider, MockVideoProvider

    if not mock_media_available():
        log.warning("ffmpeg not found — the mock generator cannot synthesise media")
        return {}

    return {
        Modality.VIDEO: MockVideoProvider(
            name="mock-video",
            cost_usd=MOCK_COST_USD[Modality.VIDEO],
            assets=_asset_factory(Modality.VIDEO),
        ),
        Modality.IMAGE: MockProvider(
            name="mock-image",
            cost_usd=MOCK_COST_USD[Modality.IMAGE],
            assets=_asset_factory(Modality.IMAGE),
        ),
        Modality.AUDIO: MockAudioProvider(
            name="mock-audio",
            cost_usd=MOCK_COST_USD[Modality.AUDIO],
            assets=_asset_factory(Modality.AUDIO),
        ),
    }


def mock_media_available() -> bool:
    from remixkit.adapters import mock_media

    return mock_media.ffmpeg_available()


def live_providers() -> dict[Modality, Any]:
    """GMI Cloud primary, ElevenLabs for audio — BUILD-SPEC §2's multi-provider story.

    Each import is guarded independently; whatever is installed and keyed is what the
    kit can fan out across. A modality with no provider is skipped at build time with a
    warning rather than failing the run.
    """
    providers: dict[Modality, Any] = {}

    try:
        from genblaze_gmicloud import GMICloudImageProvider, GMICloudVideoProvider

        providers[Modality.VIDEO] = GMICloudVideoProvider()
        providers[Modality.IMAGE] = GMICloudImageProvider()
    except Exception as exc:
        log.warning("GMI Cloud providers unavailable (%s) — video/image shots will be skipped", exc)

    try:
        from genblaze_elevenlabs import ElevenLabsProvider

        providers[Modality.AUDIO] = ElevenLabsProvider()
    except Exception as exc:
        log.warning("ElevenLabs unavailable (%s) — audio shots will be skipped", exc)

    return providers


def resolve(backend: str) -> dict[Modality, Any]:
    return mock_providers() if backend == "mock" else live_providers()
