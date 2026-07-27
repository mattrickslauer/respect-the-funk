"""Which providers a live backend actually resolves, and why a placeholder is not a key.

The bug these guard against is specific and was live in the deployed configuration: the
GMI provider classes construct successfully with no credential at all, so a deployment
whose `GMI_API_KEY` was still the Terraform placeholder reported a *live* generator and
then failed at generate time with a provider auth error. Reporting `mock` would have
been more honest; reporting live-but-broken is the one outcome the environment banner
exists to prevent.
"""

from __future__ import annotations

import pytest

from remixkit.adapters import providers
from remixkit.domain.models import Modality

PLACEHOLDER = "PLACEHOLDER — set with `aws ssm put-parameter --overwrite`"


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Start from nothing set, so a real key on the developer's machine cannot make a
    test pass that would fail in CI."""
    for name in ("GMI_API_KEY", "GOOGLE_API_KEY", "ELEVENLABS_API_KEY", "GCP_PROJECT", "GCP_LOCATION"):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------ _keyed
@pytest.mark.parametrize(
    "value,expected",
    [
        pytest.param("sk-real-looking-key", True, id="a real value"),
        pytest.param(PLACEHOLDER, False, id="the terraform placeholder"),
        pytest.param("PLACEHOLDER", False, id="the bare word"),
        pytest.param("", False, id="empty"),
        pytest.param("   ", False, id="whitespace"),
    ],
)
def test_keyed_distinguishes_a_credential_from_a_placeholder(monkeypatch, value, expected):
    monkeypatch.setenv("GMI_API_KEY", value)
    assert providers._keyed("GMI_API_KEY") is expected


def test_keyed_is_false_when_unset():
    assert providers._keyed("GMI_API_KEY") is False


# ------------------------------------------------------------------ resolution
def test_a_placeholder_key_yields_no_provider(monkeypatch):
    """The regression guard. Before this, GMI claimed video and image regardless."""
    monkeypatch.setenv("GMI_API_KEY", PLACEHOLDER)
    assert providers.live_providers() == {}


def test_no_credentials_at_all_yields_no_provider():
    assert providers.live_providers() == {}


def test_google_is_used_when_only_a_gcp_project_is_present(monkeypatch):
    """Vertex authenticates with ADC, so a project id and no API key is a complete
    credential — this is the path that works on the project's existing setup."""
    monkeypatch.setenv("GCP_PROJECT", "bidpuma")
    resolved = providers.live_providers()
    assert set(resolved) == {Modality.VIDEO, Modality.IMAGE}
    assert type(resolved[Modality.VIDEO]).__name__ == "VeoProvider"
    assert type(resolved[Modality.IMAGE]).__name__ == "ImagenProvider"


def test_gmi_wins_over_google_when_both_are_keyed(monkeypatch):
    """Order is preference, and BUILD-SPEC §2 makes GMI primary. Google is the fallback
    that keeps the pipeline working when GMI has no key, not a replacement for it."""
    monkeypatch.setenv("GMI_API_KEY", "real")
    monkeypatch.setenv("GCP_PROJECT", "bidpuma")
    resolved = providers.live_providers()
    assert type(resolved[Modality.VIDEO]).__name__ == "GMICloudVideoProvider"
    assert type(resolved[Modality.IMAGE]).__name__ == "GMICloudImageProvider"


def test_audio_is_skipped_rather_than_fatal(monkeypatch):
    """A partial provider set is the normal state — a kit without audio still runs."""
    monkeypatch.setenv("GCP_PROJECT", "bidpuma")
    assert Modality.AUDIO not in providers.live_providers()


def test_a_keyed_elevenlabs_actually_serves_audio(monkeypatch):
    """The regression guard on a two-year-old typo.

    `providers.py` imported `ElevenLabsProvider`, which genblaze-elevenlabs has never
    exported — it ships `ElevenLabsTTSProvider` and `ElevenLabsSFXProvider`. The bare
    `except` around the import turned that into a silent skip, so audio was missing even
    with a valid key and the only symptom was a modality quietly absent from the kit.
    """
    monkeypatch.setenv("ELEVENLABS_API_KEY", "real-looking-key")
    resolved = providers.live_providers()
    assert Modality.AUDIO in resolved, "a keyed ElevenLabs must actually serve audio"
    assert type(resolved[Modality.AUDIO]).__name__ == "ElevenLabsTTSProvider"


def test_mock_backend_is_unaffected_by_credentials(monkeypatch):
    """`resolve('mock')` must never consult the environment — it is what makes a laptop
    run identical to CI."""
    monkeypatch.setenv("GMI_API_KEY", "real")
    assert set(providers.resolve("mock")) == set(providers.mock_providers())
