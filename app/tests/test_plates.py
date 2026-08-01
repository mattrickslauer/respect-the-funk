"""Character plates — the artist's face, as an image the provider actually receives.

`Identity.reference_frames` has been on the model since it was written. The console could
upload them, store them, and render them, and **nothing read them**: `_compose_prompt` was
text-only, so a label that spent an afternoon photographing an artist across four lighting
setups was buying video conditioned on a sentence.

The mechanism was already there and unused. `pipeline.step(external_inputs=[Asset])` seeds
`Step.inputs`, and the GMI registry routes those into the model's native slots —
`route_images(slots=("first_frame","last_frame"))` for every `seedance-*` slug. What was
missing was ours.

The sharp edge these tests exist for: **providers disagree about image inputs, and getting
it wrong fails the entire kit rather than one shot.** `GMICloudVideoProvider` and
`SoraProvider` declare `accepts_chain_input=True`; `VeoProvider` and `ImagenProvider`
declare neither, and `Pipeline._check_step_capabilities` raises `GenblazeError` before any
step runs. Since provider selection happens at runtime from whatever is keyed, the same
brief conditions correctly on GMI and would refuse outright on Google.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import Identity, Modality, ReferenceFrame


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def artist(container, principal):
    made = container.artists.create(principal, name="Amanda Kurt")
    container.artists.set_consent(
        principal, made.id, granted=True, signed_by="manager@label.example"
    )
    return container.artists.get(principal, made.id)


@pytest.fixture
def song(container, principal, artist):
    made = container.songs.create(principal, artist.id, title="Un Poquito Mas")
    return container.songs.set_measurement(principal, made.id, bpm=96.0, bpm_method="manual entry")


def frame(name: str, lighting: str = "neutral", *, digest: str | None = None) -> ReferenceFrame:
    return ReferenceFrame(
        key=f"remixkit/frames/{name}.png",
        lighting=lighting,
        sha256=digest if digest is not None else f"{name:0>64}"[:64],
        content_type="image/png",
    )


# ------------------------------------------------------------------ selection
def test_one_plate_per_lighting_setup():
    """The reason `ReferenceFrame.lighting` exists, enforced rather than documented.

    Four exposures of one setup generalise to that setup. The upload set is raw material;
    the plate set is the product.
    """
    identity = Identity(
        tenant_id="t",
        artist_id="art_1",
        reference_frames=[
            frame("a", "neutral"),
            frame("b", "neutral"),
            frame("c", "warm"),
            frame("d", "hard-flash"),
        ],
    )
    assert [f.lighting for f in identity.plates(limit=4)] == ["neutral", "warm", "hard-flash"]


def test_exact_duplicates_are_dropped_by_content_hash():
    """The same still uploaded twice is one reference, not two."""
    identity = Identity(
        tenant_id="t",
        artist_id="art_1",
        reference_frames=[
            frame("a", "neutral", digest="d" * 64),
            frame("b", "warm", digest="d" * 64),
            frame("c", "hard-flash"),
        ],
    )
    keys = [f.key for f in identity.plates(limit=4)]
    assert len(keys) == 2, "a duplicate that survived would crowd out a different setup"


def test_a_video_shot_takes_exactly_one_plate(container, principal, song, artist):
    """Not a budget cap — a correctness one.

    Seedance routes a second image into `last_frame`, which the model reads as "end the
    clip here" and interpolates towards. Two plates on one shot requests a morph.
    """
    container.identities.create_version(
        principal,
        artist.id,
        structural_features="high cheekbones, wide-set eyes",
        reference_frames=[frame("a", "neutral"), frame("b", "warm"), frame("c", "hard-flash")],
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert len(video.reference_keys) == 1


# ------------------------------------------------------------------ attachment
def test_the_face_reaches_the_shot_that_wants_it(container, principal, song, artist):
    container.identities.create_version(
        principal,
        artist.id,
        structural_features="high cheekbones",
        reference_frames=[frame("a", "neutral")],
    )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, hook_lines=["one line"]
    )
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)
    card = next(s for s in plan.shots if s.modality is Modality.IMAGE)

    assert video.reference_keys == ["remixkit/frames/a.png"]
    assert "identity v" in video.plate_note

    # A lyric card is a typographic plate. A face reference on it produces a portrait
    # with words over it, so it deliberately does not opt in.
    assert card.reference_keys == []
    assert card.plate_note is None


def test_an_identity_with_no_frames_says_so_rather_than_going_quiet(
    container, principal, song, artist
):
    """Text-only identities used to be indistinguishable from conditioned ones."""
    container.identities.create_version(principal, artist.id, structural_features="high cheekbones")

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.reference_keys == []
    assert "no reference frames" in video.plate_note


def test_an_unhashed_frame_still_conditions_but_is_flagged(container, principal, song, artist):
    """It works. It costs a stable cache key and a stable manifest hash, and says so."""
    container.identities.create_version(
        principal,
        artist.id,
        reference_frames=[ReferenceFrame(key="remixkit/frames/legacy.png", lighting="neutral")],
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.reference_keys == ["remixkit/frames/legacy.png"]
    assert "no content hash" in video.plate_note


# ------------------------------------------------------------------ the sharp edge
def _refuse_images(provider) -> None:
    """Give a real provider `VeoProvider`'s capability declaration, in place.

    Patched onto the instance rather than wrapped in a stand-in, because a wrapper changes
    `type(provider).__name__` and that is the key `providers.DEFAULT_MODELS` resolves a
    model by — so the shot would be skipped for a missing model and the test would pass
    while asserting nothing about plates.

    The declaration is not a straw man: `genblaze_google.provider.VeoProvider` returns
    `supported_inputs=["text"]` and leaves `accepts_chain_input` at its `False` default,
    and `genblaze_google.imagen.ImagenProvider` does the same.
    """
    from genblaze_core.providers.base import ProviderCapabilities

    provider.get_capabilities = lambda: ProviderCapabilities(
        supported_inputs=["text"], accepts_chain_input=False
    )


def test_a_provider_that_refuses_images_does_not_take_the_whole_run_down(
    container, principal, song, artist, monkeypatch
):
    """The failure this gate exists for.

    `Pipeline._check_step_capabilities` raises `GenblazeError` when a step carries
    `external_inputs` and the provider has not declared it accepts them — before any step
    runs, so the whole kit fails. The plate is dropped instead, and the reason is on the
    screen where the spend is decided.
    """
    from remixkit.adapters import providers as provider_table

    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    table = provider_table.resolve("mock")
    _refuse_images(table[Modality.VIDEO])
    monkeypatch.setattr(provider_table, "resolve", lambda backend: table)

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.runs, "the shot must still run, just without the face"
    assert video.reference_keys == []
    assert "does not accept image inputs" in video.plate_note


def test_a_live_backend_on_local_storage_refuses_rather_than_sending_a_dead_url(
    settings, container, principal, song, artist, monkeypatch
):
    """The laptop-passes / Batch-fails shape, caught before the spend.

    A conditioning image is fetched by the provider from its own network. `LocalStorage`
    presigns to `/files/…`, which is an app route, not an address — so a live run would
    hand GMI a path it resolves against its own host. The mock generator fetches nothing
    and is deliberately exempt.
    """
    from remixkit.adapters import providers as provider_table

    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    # Same storage and the same providers — only the generator's backend label changes.
    # The table has to be pinned as well, because `_backend` is also what decides between
    # `mock_providers()` and `live_providers()`, and nothing is keyed in a test process.
    table = provider_table.resolve("mock")
    monkeypatch.setattr(provider_table, "resolve", lambda backend: table)
    monkeypatch.setattr(container.generator, "_backend", "genblaze")

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.reference_keys == []
    assert "RK_STORAGE_BACKEND=b2" in video.plate_note
    assert not container.storage.serves_public_urls


def test_unspecified_capabilities_are_permissive(container, principal, song, artist):
    """Genblaze documents an omitted field as "no restriction", and this follows it.

    It is also what keeps the mock path exercising this code: `MockProvider` returns
    `None` from `get_capabilities`, so a stricter reading would route every test around
    the branch it is meant to cover.
    """
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.reference_keys == ["remixkit/frames/a.png"]


# ------------------------------------------------------------------ the wire
def test_the_plate_is_sent_as_an_image_asset_with_its_hash(container, principal, song, artist):
    """What `generate` actually hands Genblaze, asserted at the boundary.

    `sha256` travels with the asset because Genblaze derives both the step cache key and
    the manifest's canonical hash from it, falling back to the URL when it is absent — and
    on B2 that URL is presigned and rotates.
    """
    digest = "e" * 64
    container.identities.create_version(
        principal,
        artist.id,
        reference_frames=[
            ReferenceFrame(
                key="remixkit/frames/amanda.png",
                lighting="neutral",
                sha256=digest,
                content_type="image/png",
            )
        ],
    )
    identity = container.identities.current(principal, artist.id)

    assets = container.generator._plate_assets(["remixkit/frames/amanda.png"], identity)

    assert len(assets) == 1
    assert assets[0].sha256 == digest
    assert assets[0].media_type == "image/png"
    assert assets[0].url, "a plate with no fetchable URL conditions nothing"


def test_a_plate_that_cannot_be_presigned_is_dropped_not_raised(
    container, principal, artist, monkeypatch
):
    """A broken reference image costs a kit its likeness, not its whole run."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    identity = container.identities.current(principal, artist.id)

    def boom(*_args, **_kwargs):
        raise RuntimeError("bucket says no")

    monkeypatch.setattr(container.storage, "presign_get", boom)

    assert container.generator._plate_assets(["remixkit/frames/a.png"], identity) == []


def test_a_kit_with_a_plate_generates(container, principal, song, artist):
    """End to end on the mock path, which reaches `pipeline.step(external_inputs=...)`."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    kit = container.kits.request(principal, song_id=song.id, video_count=1)
    ran = container.kits.run(principal.tenant_id, kit.id)

    assert ran.status.value == "ready", ran.error
    assert ran.assets


# ------------------------------------------------------------------ upload
def test_an_uploaded_frame_records_its_full_hash_and_type(client, container, principal):
    """The key carries sixteen characters of the digest; the frame carries all of it.

    Without the full hash the plate reaches a provider unhashed, and Genblaze keys the
    cache and the manifest off a presigned URL that rotates on every render.
    """
    import hashlib

    made = container.artists.create(principal, name="Amanda Kurt")
    identity = container.identities.create_version(principal, made.id, structural_features="cheekbones")

    data = b"\x89PNG\r\n\x1a\n" + b"pretend-pixels"
    response = client.post(
        f"/ui/identities/{identity.id}/frames",
        files={"file": ("face.png", data, "image/png")},
        data={"lighting": "hard-flash", "caption": "studio"},
    )
    assert response.status_code == 200

    stored = container.identities.current(principal, made.id)
    added = stored.reference_frames[-1]
    assert added.sha256 == hashlib.sha256(data).hexdigest()
    assert added.content_type == "image/png"
    assert added.lighting == "hard-flash"
