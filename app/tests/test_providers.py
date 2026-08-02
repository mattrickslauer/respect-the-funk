"""Which providers a live backend actually resolves, and why a placeholder is not a key.

The bug these guard against is specific and was live in the deployed configuration: the
GMI provider classes construct successfully with no credential at all, so a deployment
whose `GMI_API_KEY` was still the Terraform placeholder reported a *live* generator and
then failed at generate time with a provider auth error. Reporting `mock` would have
been more honest; reporting live-but-broken is the one outcome the environment banner
exists to prevent.
"""

from __future__ import annotations

import base64

import pytest

from remixkit.adapters import providers
from remixkit.domain.models import Modality

PLACEHOLDER = "PLACEHOLDER — set with `aws ssm put-parameter --overwrite`"


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Start from nothing set, so a real key on the developer's machine cannot make a
    test pass that would fail in CI."""
    for name in (
        "GMI_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ELEVENLABS_API_KEY",
        "GCP_PROJECT",
        "GCP_LOCATION",
        "RK_VIDEO_PROVIDER",
        "RK_IMAGE_PROVIDER",
        "RK_AUDIO_PROVIDER",
    ):
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


# ------------------------------------------------------------------ openai
def test_openai_serves_every_modality_when_it_is_the_only_key(monkeypatch):
    """An API key is the only credential that works in Lambda, so OpenAI is the fallback
    that makes a deployed kit possible at all."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")
    resolved = providers.live_providers()
    assert set(resolved) == {Modality.VIDEO, Modality.IMAGE, Modality.AUDIO}
    assert type(resolved[Modality.VIDEO]).__name__ == "SoraProvider"
    assert type(resolved[Modality.IMAGE]).__name__ == "DalleProvider"


def test_gmi_still_wins_over_openai(monkeypatch):
    monkeypatch.setenv("GMI_API_KEY", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    resolved = providers.live_providers()
    assert type(resolved[Modality.VIDEO]).__name__ == "GMICloudVideoProvider"


def test_openai_beats_google_because_vertex_cannot_authenticate_in_lambda(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("GCP_PROJECT", "bidpuma")
    resolved = providers.live_providers()
    assert type(resolved[Modality.VIDEO]).__name__ == "SoraProvider"


def test_elevenlabs_still_wins_audio_over_openai_tts(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "real")
    resolved = providers.live_providers()
    assert type(resolved[Modality.AUDIO]).__name__ == "ElevenLabsTTSProvider"


# ------------------------------------------------------------------ default_model
def test_every_resolvable_provider_has_a_default_model(monkeypatch):
    """The regression guard on a model id outliving the provider it belonged to.

    `settings.video_model` used to default to a GMI slug and was passed to whichever
    provider resolved, so an unkeyed GMI meant `seedance-2-0-260128` being sent to Sora.
    Every provider this module can hand back must name its own model for the modality it
    was resolved for.
    """
    for env in ("GMI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(env, "real")
    monkeypatch.setenv("GCP_PROJECT", "bidpuma")

    seen = 0
    for table in (providers.live_providers(), providers.mock_providers()):
        for modality, provider in table.items():
            assert providers.default_model(provider, modality), (
                f"{type(provider).__name__} serves {modality.value} with no default model"
            )
            seen += 1
    assert seen, "no providers resolved — the guard would pass vacuously"


def test_default_model_is_none_for_an_unknown_provider():
    assert providers.default_model(object(), Modality.VIDEO) is None


def test_mock_backend_is_unaffected_by_credentials(monkeypatch):
    """`resolve('mock')` must never consult the environment — it is what makes a laptop
    run identical to CI."""
    monkeypatch.setenv("GMI_API_KEY", "real")
    assert set(providers.resolve("mock")) == set(providers.mock_providers())


# ------------------------------------------------------------------ vendor pinning
def test_video_can_be_pinned_to_openai_over_a_keyed_gmi(monkeypatch):
    """The operational lever. GMI's `seedance-2` failed twice and billed for output it
    never produced on 2026-07-31, so moving video to Sora has to be a deploy-time setting
    rather than an edit — GMI stays keyed and keeps serving image."""
    monkeypatch.setenv("GMI_API_KEY", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("RK_VIDEO_PROVIDER", "openai")

    resolved = providers.live_providers()
    assert type(resolved[Modality.VIDEO]).__name__ == "SoraProvider"
    assert type(resolved[Modality.IMAGE]).__name__ == "GMICloudImageProvider"


def test_a_pin_naming_an_unavailable_vendor_degrades_rather_than_deleting_the_modality(monkeypatch):
    """A typo in an environment variable must not silently remove video from every kit."""
    monkeypatch.setenv("GMI_API_KEY", "real")
    monkeypatch.setenv("RK_VIDEO_PROVIDER", "opnai")  # typo

    resolved = providers.live_providers()
    assert Modality.VIDEO in resolved
    assert type(resolved[Modality.VIDEO]).__name__ == "GMICloudVideoProvider"


def test_an_empty_pin_uses_the_preference_order(monkeypatch):
    monkeypatch.setenv("GMI_API_KEY", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("RK_VIDEO_PROVIDER", "")
    assert type(providers.live_providers()[Modality.VIDEO]).__name__ == "GMICloudVideoProvider"


def test_pinning_audio_to_openai_beats_a_keyed_elevenlabs(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("RK_AUDIO_PROVIDER", "openai")
    assert type(providers.live_providers()[Modality.AUDIO]).__name__ == "OpenAITTSProvider"


def test_vendor_of_names_every_resolvable_provider(monkeypatch):
    """`vendor_of` feeds the health check, so an "unknown" there is a banner that lies."""
    for env in ("GMI_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(env, "real")
    for provider in providers.live_providers().values():
        assert providers.vendor_of(provider) != "unknown", type(provider).__name__


# ------------------------------------------------------------------ duration snapping
def test_sora_only_accepts_its_discrete_durations(monkeypatch):
    """The live failure: `Invalid seconds=10. Must be one of {8, 4, 12}`.

    A loop length is a musical quantity — `briefs._loop_seconds` derives it from the
    measured hook window — but Sora enforces a discrete set client-side
    (`genblaze_openai.provider._VALID_SECONDS`).
    """
    assert providers.snap_duration("sora-2", 10.0) == 8
    assert providers.snap_duration("sora-2", 12.0) == 12
    assert providers.snap_duration("sora-2", 4.0) == 4


def test_snapping_rounds_down_so_a_loop_never_overruns_its_hook():
    """Nearest-wins would be wrong. The requested length *is* the hook window, so a
    longer clip runs past the section it was cut to — which is exactly what a fan
    copying the template would notice."""
    assert providers.snap_duration("sora-2", 11.9) == 8  # not 12
    assert providers.snap_duration("sora-2", 7.9) == 4  # not 8


def test_below_the_smallest_allowed_duration_the_smallest_is_used():
    """There is no honest choice under the floor; the caller records that the brief
    could not be met exactly."""
    assert providers.snap_duration("sora-2", 3.0) == 4


def test_continuous_models_get_an_integer(monkeypatch):
    """GMI declares IntSchema(min=1, max=60) — any whole number, but *whole*.
    `_loop_seconds` returns one decimal place, which is the other half of this bug."""
    assert providers.snap_duration("seedance-2-0-260128", 9.3) == 9
    assert providers.snap_duration("Kling-Text2Video-V2.1-Master", 6.0) == 6
    assert isinstance(providers.snap_duration("seedance-2-0-260128", 9.3), int)


def test_continuous_models_are_clamped_to_their_schema_bounds():
    assert providers.snap_duration("seedance-2-0-260128", 900.0) == 60
    assert providers.snap_duration("seedance-2-0-260128", 0.4) == 1


def test_no_duration_asked_means_no_duration_sent():
    assert providers.snap_duration("sora-2", None) is None
    assert providers.snap_duration("sora-2", 0) is None


def test_every_default_video_model_can_render_a_default_loop():
    """The guard that would have caught this before it reached the UI: every video model
    the app can resolve must accept the loop lengths `briefs` actually produces."""
    from remixkit.services.briefs import DEFAULT_LOOP_S, MAX_LOOP_S, MIN_LOOP_S

    video_models = [
        m[Modality.VIDEO] for m in providers.DEFAULT_MODELS.values() if Modality.VIDEO in m
    ]
    assert video_models
    for model in video_models:
        for requested in (MIN_LOOP_S, DEFAULT_LOOP_S, MAX_LOOP_S, 9.3):
            snapped = providers.snap_duration(model, requested)
            allowed = providers.MODEL_DURATIONS.get(model)
            if allowed:
                assert snapped in allowed, f"{model} would reject {snapped}"
            else:
                assert isinstance(snapped, int) and 1 <= snapped <= 60


# ------------------------------------------------------- Sora's input_reference
#
# Three rejections, in order, because each refuted the fix before it.
#
#   1. The connector uploads the frame as a file, and the server refused it:
#        Invalid type for 'input_reference': expected an object, but got a file instead.
#   2. Sending the object through `videos.create()` was refused by the SDK, before any
#      request went out:
#        Expected entry at `input_reference` to be bytes, an io.IOBase instance, PathLike
#        or a tuple but received <class 'dict'> instead.
#   3. Which is not a contradiction. `POST /v1/videos` declares exactly one request content
#      type — multipart/form-data — and `input_reference` as `anyOf[binary, ImageRefParam]`.
#      The object is a multipart *field*, and the typed method only implements the file
#      half of its own union.
#
# So these assert on the bytes that reach the wire rather than on what a stub was handed.
# A test against a fake client passed for every one of the three shapes above.


@pytest.fixture
def sora():
    """The corrected provider, with no client and no credential."""
    pytest.importorskip("genblaze_openai")
    import genblaze_openai

    from remixkit.adapters import pricing
    from remixkit.adapters.provider_sora import sora_provider_class

    cls = sora_provider_class()
    return cls(models=pricing.priced_registry(genblaze_openai.SoraProvider))


@pytest.fixture
def wire(sora, monkeypatch):
    """Install a real `openai.OpenAI` whose transport records the request it would send.

    Real client, real serializer, fake socket — so the multipart encoding under test is
    the SDK's own rather than this file's idea of it.
    """
    import httpx
    import openai

    sent: list = []

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "video_123", "created_at": 0, "model": "sora-2", "object": "video",
                "progress": 0, "seconds": "8", "size": "720x1280", "status": "queued",
            },
        )

    client = openai.OpenAI(
        api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(sora, "_get_client", lambda: client)
    return sent


def _asset(url):
    from genblaze_core.models.asset import Asset as GBAsset

    return GBAsset(url=url, media_type="image/png", sha256="a" * 64)


def _step(model="sora-2", prompt="a portrait", **params):
    from genblaze_core.models.enums import Modality as GBModality
    from genblaze_core.models.step import Step

    return Step(
        provider="openai-sora", model=model, prompt=prompt,
        modality=GBModality.VIDEO, params=params,
    )


def _body(request):
    return request.content.decode("utf-8", "replace")


def test_a_url_reference_travels_as_a_multipart_field_not_a_file(sora, wire):
    """All three rejections in one assertion: multipart transport, object shape, and a
    field rather than an upload."""
    step = _step(seconds=8, size="720x1280")
    step.inputs = [_asset("https://b2/still.png")]

    assert sora.submit(step) == "video_123"

    request = wire[0]
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = _body(request)
    assert 'name="input_reference[image_url]"' in body
    assert "https://b2/still.png" in body
    assert "filename=" not in body, "an upload is what the server refused"


def test_a_chained_still_is_inlined_because_it_has_no_address_yet(sora, wire, tmp_path, monkeypatch):
    """A chain input is the previous step's output before the sink has uploaded it — a
    local path, so there is no URL to hand OpenAI and the bytes travel instead."""
    still = tmp_path / "still.png"
    still.write_bytes(b"\x89PNG\r\n\x1a\nnot-really-a-png")
    monkeypatch.setattr(sora, "_output_dir", tmp_path)

    step = _step()
    step.inputs = [_asset(still.as_uri())]
    assert sora.submit(step) == "video_123"

    body = _body(wire[0])
    assert 'name="input_reference[image_url]"' in body
    prefix = "data:image/png;base64,"
    assert prefix in body
    encoded = body.split(prefix, 1)[1].split("\r\n", 1)[0]
    assert base64.b64decode(encoded) == still.read_bytes()


def test_a_reference_too_large_to_send_is_refused_by_name(sora, tmp_path, monkeypatch):
    """`ImageRefParam.image_url` is capped at 20MiB, and base64 costs a third on top of a
    photograph. A refusal naming the frame beats a 400 naming a field."""
    from genblaze_core.exceptions import ProviderError

    from remixkit.adapters import provider_sora

    monkeypatch.setattr(provider_sora, "MAX_IMAGE_URL", 32)
    still = tmp_path / "still.png"
    still.write_bytes(b"x" * 4096)
    monkeypatch.setattr(sora, "_output_dir", tmp_path)

    with pytest.raises(ProviderError, match="over the"):
        sora._input_reference(still.as_uri())


def test_a_shot_with_no_reference_stays_on_the_connectors_typed_path(sora, wire):
    """Sora renders unlocked shots too. Nothing to correct means nothing overridden, and a
    missing face is the plan screen's refusal to make rather than a 400 with the run half
    spent."""
    assert sora.submit(_step()) == "video_123"

    body = _body(wire[0])
    assert "input_reference" not in body


def test_the_correction_keeps_the_name_the_rest_of_the_table_looks_up(sora):
    """Two lookups key on the class name — the default video model and which vendor
    answered — so renaming the subclass would silently cost Sora both."""
    assert type(sora).__name__ == "SoraProvider"
    assert providers.vendor_of(sora) == "openai"
    assert providers.default_model(sora, Modality.VIDEO) == "sora-2"
