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
import os
from typing import Any

from remixkit.adapters import pricing
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


# Which model each provider is asked for, per modality. This table exists because a
# model id is *provider-specific* and provider selection is a runtime decision: with GMI
# unkeyed, video falls to Sora or Veo, and sending them `seedance-2-0-260128` is a
# guaranteed 404. Settings still override (see `deps.py`), but they now default to empty
# so the operator opts in rather than silently pinning a GMI id onto whoever answers.
DEFAULT_MODELS: dict[str, dict[Modality, str]] = {
    "GMICloudVideoProvider": {Modality.VIDEO: "seedance-2-0-260128"},
    "GMICloudImageProvider": {Modality.IMAGE: "seedream-5.0-lite"},
    "SoraProvider": {Modality.VIDEO: "sora-2"},
    "DalleProvider": {Modality.IMAGE: "gpt-image-1"},
    "OpenAITTSProvider": {Modality.AUDIO: "gpt-4o-mini-tts"},
    "VeoProvider": {Modality.VIDEO: "veo-3.0-generate-001"},
    "ImagenProvider": {Modality.IMAGE: "imagen-4.0-generate-001"},
    "ElevenLabsTTSProvider": {Modality.AUDIO: "eleven_multilingual_v2"},
    "MockVideoProvider": {Modality.VIDEO: "mock-video"},
    "MockProvider": {Modality.IMAGE: "mock-image"},
    "MockAudioProvider": {Modality.AUDIO: "mock-audio"},
}


# Which clip lengths a model will actually accept, in seconds.
#
# A loop length is a *musical* quantity here — `services/briefs._loop_seconds` derives it
# from the measured hook window — but every video model has its own opinion about which
# durations exist, and they do not agree. Sora enforces a discrete set client-side
# (`genblaze_openai.provider._VALID_SECONDS`); the GMI models declare `IntSchema(min=1,
# max=60)`, i.e. any whole number. A model absent from this table is treated as
# continuous.
#
# The float matters as much as the set: `_loop_seconds` returns one decimal place (9.3),
# and every one of these models wants an integer.
MODEL_DURATIONS: dict[str, frozenset[int]] = {
    "sora-2": frozenset({4, 8, 12}),
    "sora-2-pro": frozenset({4, 8, 12}),
}

# Continuous models still have bounds — GMI's schema is 1..60.
CONTINUOUS_MIN, CONTINUOUS_MAX = 1, 60


def snap_duration(model: str | None, seconds: float | None) -> int | None:
    """The nearest length this model will accept, never longer than what was asked.

    Rounding *down* to an allowed value is deliberate rather than nearest-wins. The
    requested length is the hook window, so a longer clip runs past the end of the
    section it was cut to — which is precisely the thing a fan trying to copy the
    template would notice. A loop shorter than its hook still sits inside it.

    Below the smallest allowed value there is no honest choice, so the smallest is used
    and the caller records that the brief could not be met exactly.
    """
    if not seconds or seconds <= 0:
        return None

    allowed = MODEL_DURATIONS.get(model or "")
    if allowed:
        fitting = [d for d in allowed if d <= seconds]
        return max(fitting) if fitting else min(allowed)

    return max(CONTINUOUS_MIN, min(CONTINUOUS_MAX, int(seconds)))


def default_model(provider: Any, modality: Modality) -> str | None:
    """The model this particular provider should be asked for, or None if unknown."""
    return DEFAULT_MODELS.get(type(provider).__name__, {}).get(modality)


def _keyed(name: str) -> bool:
    """Is this credential actually set, as opposed to present-but-placeholder?

    Terraform seeds every SSM parameter with a `PLACEHOLDER …` string so the parameter
    exists before anyone has a key for it, and `bootstrap.load_secrets` skips those — so
    the usual failure is not a missing variable but an empty one.

    This matters because the GMI providers construct happily with no credential at all.
    Without this check a keyless deployment reports a *live* generator and then fails at
    generate time with a provider auth error, which is strictly worse than reporting
    `mock`: the console claims something it cannot do. Checking here turns that into a
    provider that is simply absent, which is a state the rest of the code handles.
    """
    value = os.environ.get(name, "").strip()
    return bool(value) and not value.startswith("PLACEHOLDER")


# Vendor names accepted by `RK_VIDEO_PROVIDER` / `RK_IMAGE_PROVIDER` /
# `RK_AUDIO_PROVIDER`, and the order they are preferred in when none is pinned.
#
# GMI first by BUILD-SPEC §2 intent, then OpenAI, then Google. OpenAI sits ahead of
# Google for a deployment reason rather than a quality one: an API key authenticates
# anywhere, whereas Vertex resolves Application Default Credentials — a laptop
# credential that does not exist inside Lambda or a Batch task. Ordering Google higher
# would mean prod resolving a provider that cannot authenticate.
VENDOR_ORDER: dict[Modality, tuple[str, ...]] = {
    Modality.VIDEO: ("gmicloud", "openai", "google"),
    Modality.IMAGE: ("gmicloud", "openai", "google"),
    Modality.AUDIO: ("elevenlabs", "openai"),
}


def _available_vendors() -> dict[Modality, dict[str, Any]]:
    """Every provider that is installed *and* keyed, grouped by modality and vendor.

    Built in full before anything is chosen, so that pinning a vendor is a lookup rather
    than a re-ordering of construction. A vendor that fails to import or construct is
    simply absent — a partial provider set is the normal state.
    """
    table: dict[Modality, dict[str, Any]] = {m: {} for m in VENDOR_ORDER}

    if _keyed("GMI_API_KEY"):
        try:
            from genblaze_gmicloud import GMICloudImageProvider, GMICloudVideoProvider

            table[Modality.VIDEO]["gmicloud"] = GMICloudVideoProvider(
                models=pricing.priced_registry(GMICloudVideoProvider)
            )
            table[Modality.IMAGE]["gmicloud"] = GMICloudImageProvider(
                models=pricing.priced_registry(GMICloudImageProvider)
            )
        except Exception as exc:
            log.warning("GMI Cloud unavailable (%s)", exc)
    else:
        log.info("GMI_API_KEY is not set — skipping GMI Cloud")

    if _keyed("OPENAI_API_KEY"):
        try:
            from genblaze_openai import DalleProvider, OpenAITTSProvider, SoraProvider

            table[Modality.VIDEO]["openai"] = SoraProvider(models=pricing.priced_registry(SoraProvider))
            table[Modality.IMAGE]["openai"] = DalleProvider(models=pricing.priced_registry(DalleProvider))
            table[Modality.AUDIO]["openai"] = OpenAITTSProvider(
                models=pricing.priced_registry(OpenAITTSProvider)
            )
        except Exception as exc:
            log.warning("OpenAI unavailable (%s)", exc)
    else:
        log.info("OPENAI_API_KEY is not set — skipping OpenAI")

    google_project = os.environ.get("GCP_PROJECT", "").strip()
    if _keyed("GOOGLE_API_KEY") or google_project:
        try:
            from genblaze_google import ImagenProvider, VeoProvider

            # api_key=None makes the provider take the Vertex path and resolve
            # Application Default Credentials, which is the only Google auth that still
            # works — express-mode keys were withdrawn (see content/bin/vertex.py).
            kwargs: dict[str, Any] = {
                "api_key": os.environ.get("GOOGLE_API_KEY") or None,
                "location": os.environ.get("GCP_LOCATION", "us-central1").strip() or "us-central1",
            }
            if google_project:
                kwargs["project"] = google_project

            table[Modality.VIDEO]["google"] = VeoProvider(**kwargs)
            table[Modality.IMAGE]["google"] = ImagenProvider(**kwargs)
        except Exception as exc:
            log.warning("Google (Veo/Imagen) unavailable (%s)", exc)
    else:
        log.info("neither GOOGLE_API_KEY nor GCP_PROJECT is set — skipping Google")

    if _keyed("ELEVENLABS_API_KEY"):
        try:
            # `ElevenLabsTTSProvider`, not `ElevenLabsProvider` — the latter has never
            # existed in genblaze-elevenlabs, so this import raised ImportError on every
            # run and a bare `except` swallowed it. The package splits speech from sound
            # effects; the kit brief wants speech.
            from genblaze_elevenlabs import ElevenLabsTTSProvider

            table[Modality.AUDIO]["elevenlabs"] = ElevenLabsTTSProvider(
                models=pricing.priced_registry(ElevenLabsTTSProvider)
            )
        except Exception as exc:
            log.warning("ElevenLabs unavailable (%s)", exc)
    else:
        log.info("ELEVENLABS_API_KEY is not set")

    return table


def _pinned(modality: Modality) -> str:
    """`RK_VIDEO_PROVIDER` and friends, read straight from the environment.

    Read here rather than threaded through `Settings` because `resolve()` is called from
    the generator, the budget estimate, and the health check, and none of them holds a
    settings object — the same reason `bootstrap.export_for_libs` exists.
    """
    return os.environ.get(f"RK_{modality.name}_PROVIDER", "").strip().lower()


def live_providers() -> dict[Modality, Any]:
    """Whatever is installed *and* actually keyed, one provider per modality.

    A vendor may be pinned per modality — `RK_VIDEO_PROVIDER=openai` sends video to Sora
    even though GMI is keyed and would otherwise win. That exists because provider
    preference is an *operational* decision, not a code one: on 2026-07-31 GMI's
    `seedance-2` failed twice and billed for output it never produced, and the fix has to
    be a deploy-time setting rather than an edit.

    A pin naming a vendor that is unavailable falls through to the normal order with a
    warning, rather than leaving the modality unserved — a typo in an environment
    variable should degrade, not silently delete video from every kit.
    """
    available = _available_vendors()
    providers: dict[Modality, Any] = {}

    for modality, order in VENDOR_ORDER.items():
        candidates = available.get(modality) or {}
        if not candidates:
            continue

        pin = _pinned(modality)
        if pin:
            if pin in candidates:
                providers[modality] = candidates[pin]
                log.info("%s pinned to %s", modality.value, pin)
                continue
            log.warning(
                "RK_%s_PROVIDER=%s is not available (have: %s) — falling back to preference order",
                modality.name,
                pin,
                ", ".join(sorted(candidates)) or "none",
            )

        for vendor in order:
            if vendor in candidates:
                providers[modality] = candidates[vendor]
                break

    if not providers:
        log.warning(
            "generator_backend=genblaze but no provider is credentialed — every shot "
            "will be skipped. Set OPENAI_API_KEY or GMI_API_KEY."
        )
    return providers


def vendor_of(provider: Any) -> str:
    """Which vendor a resolved provider belongs to — for the health check and banner."""
    name = type(provider).__name__
    if name.startswith("GMICloud"):
        return "gmicloud"
    if name in {"SoraProvider", "DalleProvider", "OpenAITTSProvider"}:
        return "openai"
    if name in {"VeoProvider", "ImagenProvider"}:
        return "google"
    if name.startswith("ElevenLabs"):
        return "elevenlabs"
    if name.startswith("Mock"):
        return "mock"
    return "unknown"


def resolve(backend: str) -> dict[Modality, Any]:
    return mock_providers() if backend == "mock" else live_providers()
