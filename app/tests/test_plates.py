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


def conditioned(shot):
    """The stage that actually carries the reference images.

    A video shot with an identity lock delegates its plates to the still it is generated
    from — the clip is conditioned on the still, not on the raw frames, because putting
    both in the same positional slots asks the model to interpolate between them. So the
    stage under test is the prelude where there is one, and the clip itself where the lock
    degraded (no image provider, no frames, or a provider that refuses images).
    """
    return shot.prelude or shot


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

    # The clip carries none: it is conditioned on the still, which is where the face was
    # settled. The still carries one, because no reference slot is configured — see
    # `GenblazeGenerator._reference_limit`.
    assert video.reference_keys == []
    assert len(video.prelude.reference_keys) == 1


# ------------------------------------------------------------------ attachment
def test_the_face_reaches_the_shot_that_wants_it(container, principal, song, artist):
    container.identities.create_version(
        principal,
        artist.id,
        structural_features="high cheekbones",
        reference_frames=[frame("a", "neutral")],
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert conditioned(video).reference_keys == ["remixkit/frames/a.png"]
    assert "identity v" in conditioned(video).plate_note

    # The clip itself carries none — it is conditioned on the still, and handing it the
    # raw plates as well would put two different images in the same positional slots.
    assert video.reference_keys == []


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

    assert conditioned(video).reference_keys == ["remixkit/frames/legacy.png"]
    assert "no content hash" in conditioned(video).plate_note


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
    # And no still was built for it. `input_from` is validated under the same rule as a
    # plate, so a still rendered for a provider that refuses images is a frame that is
    # billed and then kills the run it was rendered for.
    assert video.prelude is None


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

    assert conditioned(video).reference_keys == ["remixkit/frames/a.png"]


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

    assets = container.generator._plate_assets(["remixkit/frames/amanda.png"], [identity])

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

    assert container.generator._plate_assets(["remixkit/frames/a.png"], [identity]) == []


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


# ------------------------------------------------------------------ the identity lock
def test_a_locked_clip_is_generated_from_a_still_not_from_the_plates(
    container, principal, song, artist
):
    """The two-stage shot, which is the strongest likeness this stack can produce.

    A video model conditioned on text drifts across the clip; one conditioned on a first
    frame is anchored to it. So the face is settled once in a still — cheap enough to
    regenerate until it is right — and the clip inherits it.

    The clip must carry *no* plates of its own. Seedance's slots are positional, so a clip
    handed both the still and the raw reference would have two different images in
    `first_frame`/`last_frame` and be asked to interpolate between them.
    """
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.prelude is not None
    assert video.prelude.modality is Modality.IMAGE
    assert video.reference_keys == []
    assert video.prelude.reference_keys == ["remixkit/frames/a.png"]


def test_both_stages_are_quoted(container, principal, song, artist):
    """The invoice counts provider calls. A shot priced as one call bills as two."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert plan.steps == 2, "one shot, two provider calls"
    assert plan.estimate_cents == video.estimate_cents + video.prelude.estimate_cents


def test_the_still_is_asked_for_a_photograph_not_a_frame_of_video(
    container, principal, song, artist
):
    """A still rendered from "vertical 9:16 loop … movement at 96 BPM" is a request for a
    frame of a video, and image models answer that with motion blur. The scene survives;
    the motion language does not."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    still = next(s for s in plan.shots if s.modality is Modality.VIDEO).prelude

    assert "Single still photograph" in still.prompt
    assert "BPM" not in still.prompt
    assert "loop" not in still.prompt.lower()
    assert "Un Poquito Mas" in still.prompt, "the two stages must agree about the scene"


def test_no_still_is_built_for_a_provider_that_refuses_images(
    container, principal, song, artist, monkeypatch
):
    """`input_from` is validated under the same rule as `external_inputs`.

    A still built for a provider that refuses image inputs is a frame that is rendered,
    billed, and then kills the run it was rendered for — the single-stage path already
    closed this hole and the two-stage path reopened it.
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

    assert video.prelude is None
    assert plan.steps == len(plan.runnable), "no orphan still was planned or priced"


def test_the_lock_degrades_when_there_is_no_image_provider(
    container, principal, song, artist, monkeypatch
):
    """Video only. The clip still runs, conditioned on the plate directly."""
    from remixkit.adapters import providers as provider_table

    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    full = provider_table.resolve("mock")
    monkeypatch.setattr(
        provider_table, "resolve", lambda backend: {Modality.VIDEO: full[Modality.VIDEO]}
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert video.runs
    assert video.prelude is None
    assert video.reference_keys == ["remixkit/frames/a.png"]


def test_only_one_reference_is_sent_until_a_slot_is_configured(
    container, principal, song, artist
):
    """The honest boundary of what can be built without the vendor's payload schema.

    Genblaze can route any number of images; what it cannot know is the key a given model
    expects them under, and every GMI image model resolves through a fallback declaring a
    single positional `image` slot that silently drops the rest. A slot name invented here
    would either 400 or — far worse — be ignored, leaving a kit that reports four
    references, is billed for one, and looks conditioned.
    """
    container.identities.create_version(
        principal,
        artist.id,
        reference_frames=[
            frame("a", "neutral"), frame("b", "warm"), frame("c", "hard-flash")
        ],
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    still = next(s for s in plan.shots if s.modality is Modality.VIDEO).prelude

    assert len(still.reference_keys) == 1
    assert container.generator._reference_slot == "", "the default must be the verified one"


def test_configuring_a_slot_sends_the_whole_classed_set(
    container, principal, song, artist, monkeypatch
):
    """And when an operator has checked the model's payload, the set goes."""
    container.identities.create_version(
        principal,
        artist.id,
        reference_frames=[
            frame("a", "neutral"), frame("b", "warm"), frame("c", "hard-flash")
        ],
    )
    monkeypatch.setattr(container.generator, "_reference_slot", "reference_images")

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    still = next(s for s in plan.shots if s.modality is Modality.VIDEO).prelude

    assert len(still.reference_keys) == 3
    assert "3 references" in still.plate_note


def test_a_locked_kit_generates_both_stages(container, principal, song, artist):
    """End to end: the still is rendered, and the clip is generated from it."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    kit = container.kits.request(principal, song_id=song.id, video_count=1)
    ran = container.kits.run(principal.tenant_id, kit.id)

    assert ran.status.value == "ready", ran.error
    assert len(ran.assets) == 2, "the still and the clip"
    assert {a.modality.value for a in ran.assets} == {"image", "video"}


# ------------------------------------------------------------------ several faces








# ------------------------------------------------------------------ the still index








# ------------------------------------------------------------------ the model must read it




def test_no_reference_model_is_guessed_for_a_vendor_that_ships_no_catalog():
    """A hardcoded slug 404'd on a real account.

    It came from the connector's `example_slugs`, which its own docstring calls
    "documentation grade, not a contract", and GMI declares `DiscoverySupport.PARTIAL` —
    no catalog endpoint to check against, and the only liveness test is a billable probe.
    Which models an account can reach is a fact only the operator has, so the answer is
    `None` and a refusal rather than another guess that moves the 404.
    """
    from remixkit.adapters import providers as provider_table
    from remixkit.domain.models import Modality

    gmi = type("GMICloudImageProvider", (), {})()
    assert provider_table.reference_model(gmi, Modality.IMAGE) is None


def test_a_configured_edit_model_is_used(container):
    """`RK_IMAGE_EDIT_MODEL` is where the operator's answer goes."""
    from remixkit.adapters import providers as provider_table
    from remixkit.domain.models import Modality

    gmi = type("GMICloudImageProvider", (), {})()
    chosen = provider_table.reference_model(gmi, Modality.IMAGE, "reve-edit-20250915")

    assert chosen == "reve-edit-20250915"
    assert provider_table.honours_reference(chosen)


def test_a_configured_model_that_cannot_read_references_is_not_used():
    """Setting the variable to a text-to-image model is the same mistake with more steps."""
    from remixkit.adapters import providers as provider_table
    from remixkit.domain.models import Modality

    gmi = type("GMICloudImageProvider", (), {})()
    assert provider_table.reference_model(gmi, Modality.IMAGE, "seedream-5.0-lite") is None






# ------------------------------------------------------------------ several faces








# ------------------------------------------------------------------ the still index








# ------------------------------------------------------------------ the model must read it






def test_a_plate_is_refused_when_the_vendor_has_no_model_that_would_read_it(
    container, principal, song, artist, monkeypatch
):
    """Imagen is text-only and Google ships no image-to-image model, so there is nothing
    to swap to. Attaching the reference anyway would render a stranger at full price."""
    from remixkit.adapters import providers as provider_table

    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    real = provider_table.resolve("mock")

    class ImagenProvider:
        def __init__(self, inner): self._i = inner
        def __getattr__(self, name): return getattr(self._i, name)
        def get_capabilities(self): return self._i.get_capabilities()

    monkeypatch.setattr(
        provider_table,
        "resolve",
        lambda backend: {**real, Modality.IMAGE: ImagenProvider(real[Modality.IMAGE])},
    )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="portrait-still"
    )
    shot = plan.shots[0]

    assert shot.reference_keys == []
    assert "would discard the reference" in shot.plate_note


# ------------------------------------------------------------------ several faces
def test_a_kit_can_be_for_more_than_one_face(container, principal, song, artist):
    """A duo. Every face's text composes into the prompt, named, because two unlabelled
    silhouettes read to a model as one contradictory person."""
    container.identities.create_version(
        principal, artist.id, presentation="feminine", build="slight",
        reference_frames=[frame("a", "neutral")],
    )
    container.identities.create_version(
        principal, artist.id, name="Marco", presentation="masculine", build="solid",
        reference_frames=[frame("b", "warm")],
    )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, identity_names=["", "Marco"]
    )
    shot = plan.shots[0]

    assert "Amanda Kurt: feminine slight" in shot.prompt, "the primary line is the artist"
    assert "Marco: masculine solid" in shot.prompt


def test_a_group_shot_says_how_many_faces_actually_reached_the_wire(
    container, principal, song, artist
):
    """The honest count. One image slot exists until `RK_REFERENCE_SLOT` is verified, so
    a duo conditioned on one member is the failure that must not be invisible."""
    for name in ("", "Marco"):
        container.identities.create_version(
            principal, artist.id, name=name,
            reference_frames=[frame(f"f{name or 'p'}", "neutral")],
        )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, identity_names=["", "Marco"]
    )
    note = plan.shots[0].prelude.plate_note

    assert "2 faces" in note and "1 reference" in note
    assert "RK_REFERENCE_SLOT" in note


def test_a_named_line_with_no_versions_is_dropped_not_substituted(
    container, principal, song, artist
):
    """A kit that quietly renders a different *person* than its brief says is worse than
    one that renders one fewer."""
    container.identities.create_version(principal, artist.id, structural_features="singer")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, identity_names=["Nobody"]
    )
    assert "no identity" in plan.shots[0].plate_note


def test_a_kit_records_which_faces_it_was_for(container, principal, song, artist):
    """By id *and* name. The id names an exact version whose content cannot move; the
    name is what a person reads without a lookup."""
    container.identities.create_version(principal, artist.id, structural_features="singer")
    container.identities.create_version(principal, artist.id, name="Marco")

    kit = container.kits.request(
        principal, song_id=song.id, video_count=1, identity_names=["", "Marco"]
    )

    assert [f["label"] for f in kit.brief["identities"]] == ["Primary", "Marco"]
    assert all(f["version"] == 1 for f in kit.brief["identities"])


# ------------------------------------------------------------------ the still index
def test_the_second_kit_in_the_same_scene_does_not_pay_for_the_still_again(
    container, principal, song, artist
):
    """The index. A still is a pure function of who is in it and the scene, so rendering
    it twice is paying twice for a stored answer."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    first = container.kits.request(principal, song_id=song.id, video_count=1)
    ran = container.kits.run(principal.tenant_id, first.id)
    assert ran.status.value == "ready", ran.error
    assert len(container.identities.current(principal, artist.id).locked_stills) == 1

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    still = plan.shots[0].prelude

    assert still.reused_still is not None
    assert still.estimate_cents == 0
    assert "reusing a stored still" in still.plate_note
    assert plan.steps == 1, "a reused still is not a provider call"


def test_a_reused_still_is_not_generated_again(container, principal, song, artist):
    """Asserted at the far end: the second run produces the clip only."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    container.kits.run(
        principal.tenant_id, container.kits.request(principal, song_id=song.id, video_count=1).id
    )

    second = container.kits.request(principal, song_id=song.id, video_count=1)
    ran = container.kits.run(principal.tenant_id, second.id)

    assert ran.status.value == "ready", ran.error
    assert [a.modality.value for a in ran.assets] == ["video"], "the still was re-rendered"


def test_a_new_identity_version_misses_the_index(container, principal, song, artist):
    """Versions are in the digest on purpose. Reusing a frame of how somebody *used* to
    look is the one way a cache here could undo the versioning discipline."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    container.kits.run(
        principal.tenant_id, container.kits.request(principal, song_id=song.id, video_count=1).id
    )

    container.identities.create_version(principal, artist.id, structural_features="new hair")

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    assert plan.shots[0].prelude.reused_still is None
    assert plan.shots[0].prelude.estimate_cents > 0


def test_a_different_scene_misses_the_index(container, principal, song, artist):
    """The scene is in the digest too — a night drive still is not a golden-hour one."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    container.kits.run(
        principal.tenant_id, container.kits.request(principal, song_id=song.id, video_count=1).id
    )

    # Two videos: the first mood is indexed, the second has never been rendered.
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=2)
    reused = [s.prelude.reused_still is not None for s in plan.shots]

    assert reused == [True, False]


# ------------------------------------------------------------------ the model must read it
def test_a_text_to_image_model_is_never_used_to_lock_a_face():
    """The failure that made this worthless without looking broken.

    `GMICloudImageProvider` declares `accepts_chain_input=True`, so the capability gate
    passed and the plate was sent — to `seedream-5.0-lite`, which is text-to-image. The
    payload carried an `image` key the model does not read, so the only thing describing
    the artist was four words of compiled silhouette, and what came back was a stranger
    with the right build.
    """
    from remixkit.adapters import providers as provider_table

    assert not provider_table.honours_reference("seedream-5.0-lite")
    assert not provider_table.honours_reference("imagen-4.0-generate-001")
    assert provider_table.honours_reference("seededit-3-0-i2i-250628")
    assert provider_table.honours_reference("gpt-image-1")


def test_an_unknown_model_is_assumed_not_to_read_references():
    """The safe direction, and the expensive one to get backwards: a model wrongly treated
    as image-capable renders a plausible stranger and bills full price, where one wrongly
    treated as text-only produces a refusal somebody can act on."""
    from remixkit.adapters import providers as provider_table

    assert not provider_table.honours_reference("some-model-nobody-has-heard-of")
    assert not provider_table.honours_reference(None)


def test_a_vendor_with_no_image_to_image_model_cannot_lock():
    """Imagen is text-only. Returning its default would render a stranger at full price."""
    from remixkit.adapters import providers as provider_table
    from remixkit.domain.models import Modality

    imagen = type("ImagenProvider", (), {})()
    assert provider_table.reference_model(imagen, Modality.IMAGE) is None


# ------------------------------------------------------------------ the swap, not the drop
@pytest.fixture
def gmi_shaped(monkeypatch):
    """A provider table with the live GMI class names.

    The mock providers are deliberately exempt from the reference check — excluding them
    would make the whole locked path unreachable on a laptop — so observing the swap needs
    providers that resolve through `DEFAULT_MODELS` and `vendor_of` the way production
    does.
    """
    from remixkit.adapters import providers as provider_table

    real = provider_table.resolve("mock")

    class GMICloudImageProvider:
        def __init__(self, inner): self._i = inner
        def __getattr__(self, name): return getattr(self._i, name)
        def get_capabilities(self): return self._i.get_capabilities()

    class GMICloudVideoProvider(GMICloudImageProvider):
        pass

    table = {
        Modality.IMAGE: GMICloudImageProvider(real[Modality.IMAGE]),
        Modality.VIDEO: GMICloudVideoProvider(real[Modality.VIDEO]),
    }
    monkeypatch.setattr(provider_table, "resolve", lambda backend: table)
    return table


def test_a_shot_that_wants_the_face_swaps_the_model_rather_than_dropping_the_reference(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """The first attempt at this fix was the wrong half.

    It refused the plate when the model could not read it, which made the failure legible
    and still rendered the picture — a portrait still went out on `seedream-5.0-lite` with
    no reference and came back as the same stranger it had produced before, now with an
    explanation nobody was looking at.
    """
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    monkeypatch.setattr(container.generator, "_edit_model", "reve-edit-20250915")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="portrait-still"
    )
    shot = plan.shots[0]

    assert shot.model == "reve-edit-20250915"
    assert shot.reference_keys == ["remixkit/frames/a.png"]
    assert "would ignore" not in (shot.plate_note or "")


def test_the_locked_still_is_checked_as_an_image_not_as_the_clip_it_serves(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """`_plates_for` read `shot.modality`, and for a locked video shot that says VIDEO —
    so the model check silently never ran on the one step whose whole job is to resolve
    the likeness."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    monkeypatch.setattr(container.generator, "_edit_model", "reve-edit-20250915")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance"
    )
    still = plan.shots[0].prelude

    assert still is not None
    assert provider_reads_references(still.model)
    assert still.reference_keys == ["remixkit/frames/a.png"]


def provider_reads_references(model: str) -> bool:
    from remixkit.adapters import providers as provider_table

    return provider_table.honours_reference(model)


def test_an_explicitly_pinned_model_is_never_swapped(
    container, principal, song, artist, gmi_shaped
):
    """Somebody who typed a model into the plan meant it. Silently running a different one
    would defeat the field — the refusal note still says what it costs them."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="portrait-still",
        overrides={0: {"model": "seedream-5.0-lite"}},
    )
    shot = plan.shots[0]

    assert shot.model == "seedream-5.0-lite"
    assert shot.reference_keys == []
    assert "would discard the reference" in shot.plate_note


# ------------------------------------------------------------------ the clip must read it too
def test_a_text_to_video_model_discards_the_still_the_lock_paid_for():
    """The half that made a corrected still change nothing.

    GMI's hub tags `seedance-2-0-260128` Text-to-Video only, while the connector declares
    `first_frame`/`last_frame` for every `seedance-*` slug — true of the 1.x line and not
    of 2.0. So the lock rendered a frame, paid for it, and handed it to a model that
    throws it away.
    """
    from remixkit.adapters import providers as provider_table
    from remixkit.domain.models import Modality

    assert not provider_table.honours_reference("seedance-2-0-260128", Modality.VIDEO)
    assert provider_table.honours_reference("seedance-1-5-pro-251215", Modality.VIDEO)
    assert provider_table.honours_reference("kling-o1-reference-to-video", Modality.VIDEO)


def test_no_still_is_rendered_for_a_clip_that_would_discard_it(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """Paying for a frame whose only consumer throws it away is worse than not locking."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    monkeypatch.setattr(container.generator, "_edit_model", "bria-fibo-edit")
    monkeypatch.setattr(container.generator, "_backend", "genblaze")  # leave the mock exemption

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance"
    )
    assert plan.shots[0].prelude is None, "a still was rendered for a model that discards it"


def test_both_stages_swap_when_configured(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """The whole chain, on models the catalog actually lists."""
    container.identities.create_version(
        principal, artist.id, reference_frames=[frame("a", "neutral")]
    )
    monkeypatch.setattr(container.generator, "_edit_model", "bria-fibo-edit")
    monkeypatch.setattr(container.generator, "_video_edit_model", "kling-o1-reference-to-video")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance"
    )
    clip = plan.shots[0]

    assert clip.model == "kling-o1-reference-to-video"
    assert clip.prelude is not None
    assert clip.prelude.model == "bria-fibo-edit"
    assert clip.prelude.reference_keys == ["remixkit/frames/a.png"]


# ------------------------------------------------------------------ payload shapes
def test_the_bria_family_sends_images_as_an_array():
    """GMI rejected the singular form by name:

        bria-fibo-edit -> 400: invalid payload parameters: images (Required parameter is
        missing)

    The connector ships a family for `bria-genfill`/`bria-eraser` only; everything else
    Bria publishes lands on the permissive fallback and goes out as `image`. A rejection
    that names the missing parameter is the one source that cannot be wrong about it.
    """
    from genblaze_core.models.asset import Asset as GBAsset
    from genblaze_gmicloud import GMICloudImageProvider

    from remixkit.adapters import pricing

    registry = pricing.priced_registry(GMICloudImageProvider)
    one = GBAsset(url="https://b2/a.png", media_type="image/png", sha256="a" * 64)

    assert registry.get("bria-fibo-edit").input_mapping([one]) == {"images": ["https://b2/a.png"]}
    # The connector's own inpaint family is more specific and must not be overridden.
    assert registry.get("bria-genfill").input_mapping([one]) == {"image": "https://b2/a.png"}


def test_an_array_slot_carries_the_whole_reference_set():
    """A slot that takes a list is a multi-reference slot, which is what a likeness needs
    and what `RK_REFERENCE_SLOT` was holding out for — except this one came from the
    vendor rather than from a guess."""
    from genblaze_core.models.asset import Asset as GBAsset
    from genblaze_gmicloud import GMICloudImageProvider

    from remixkit.adapters import pricing

    registry = pricing.priced_registry(GMICloudImageProvider)
    frames = [
        GBAsset(url=f"https://b2/{n}.png", media_type="image/png", sha256=n * 64)
        for n in ("a", "b", "c")
    ]

    assert registry.get("bria-fibo-edit").input_mapping(frames) == {
        "images": ["https://b2/a.png", "https://b2/b.png", "https://b2/c.png"]
    }


def test_a_model_with_an_array_slot_gets_the_whole_classed_set(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """Per model rather than per deployment: how many references travel is a property of
    the model, and one positional slot still gets one whatever the setting says."""
    from remixkit.domain.models import FrameAngle, ReferenceFrame

    frames = [
        ReferenceFrame(
            key=f"remixkit/frames/{angle.value}.png", angle=angle,
            sha256=angle.value.ljust(64, "0")[:64], content_type="image/png",
        )
        for angle in (FrameAngle.FRONT, FrameAngle.THREE_QUARTER_LEFT, FrameAngle.PROFILE_RIGHT)
    ]
    container.identities.create_version(principal, artist.id, reference_frames=frames)
    monkeypatch.setattr(container.generator, "_edit_model", "bria-fibo-edit")
    monkeypatch.setattr(container.generator, "_video_edit_model", "kling-o1-image-to-video")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance"
    )
    still = plan.shots[0].prelude

    assert len(still.reference_keys) == 3, "an array slot should carry the whole set"
    assert "3 references" in still.plate_note


def test_a_single_slot_model_still_gets_one(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    from remixkit.adapters import providers as provider_table

    container.identities.create_version(
        principal, artist.id,
        reference_frames=[frame("a", "neutral"), frame("b", "warm")],
    )
    monkeypatch.setattr(container.generator, "_edit_model", "seededit-3-0-i2i-250628")
    monkeypatch.setattr(container.generator, "_video_edit_model", "kling-o1-image-to-video")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance"
    )

    assert not provider_table.takes_multiple_references("seededit-3-0-i2i-250628")
    assert len(plan.shots[0].prelude.reference_keys) == 1


def test_an_edit_model_with_no_reference_is_refused_before_submission(
    container, principal, song, artist, gmi_shaped, monkeypatch
):
    """An edit model with nothing to edit is a 400, not a worse picture.

    Bria said so by name — `images (Required parameter is missing)` — and the run reached
    the provider to find out, spending a queue slot and a worker on an answer the plan
    screen already had.
    """
    # An identity with no reference frames at all.
    container.identities.create_version(principal, artist.id, structural_features="oval face")
    monkeypatch.setattr(container.generator, "_edit_model", "bria-fibo-edit")

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="portrait-still"
    )
    shot = plan.shots[0]

    assert not shot.runs
    assert "needs a reference image" in shot.skipped_reason
    # The plan total is what a person is asked to approve, and a run that cannot happen
    # must not be quoted. The shot keeps its own would-be price, which is what the screen
    # needs to explain the refusal.
    assert plan.estimate_cents == 0
    assert plan.blocker


def test_the_reference_survives_all_the_way_into_the_provider_payload():
    """The check every previous version of this fix stopped one step short of.

    Routing an input into the payload is not enough: the spec's `param_allowlist` filters
    the result immediately afterwards, and `ParamSurface.for_modality` builds that list
    from the params that are *universally meaningful* for a modality — which cannot
    include a vendor's input slot name. So the mapper inserted `images`, the filter removed
    it, and the request went out missing the parameter the model requires.

    The only trace was a log line:

        Dropping non-allowlisted params for bria-fibo-edit: ['images']

    Testing the mapper in isolation passed throughout. This asserts on
    `prepare_payload`, which is what `submit()` actually sends.
    """
    from genblaze_core.models.asset import Asset as GBAsset
    from genblaze_core.models.enums import Modality as GBModality
    from genblaze_core.models.step import Step
    from genblaze_gmicloud import GMICloudImageProvider

    from remixkit.adapters import pricing

    provider = GMICloudImageProvider(models=pricing.priced_registry(GMICloudImageProvider))
    frames = [
        GBAsset(url=f"https://b2/{n}.png", media_type="image/png", sha256=n * 64)
        for n in ("a", "b")
    ]

    step = Step(
        provider="gmicloud-image", model="bria-fibo-edit", prompt="a portrait",
        modality=GBModality.IMAGE, params={"aspect_ratio": "9:16"},
    )
    step.inputs = frames

    payload = provider.prepare_payload(step)
    assert payload.get("images") == ["https://b2/a.png", "https://b2/b.png"]


def test_the_shipped_families_still_send_their_own_slot():
    """A user family is checked first, so a broad pattern can quietly take over a model the
    connector already handled correctly."""
    from genblaze_core.models.asset import Asset as GBAsset
    from genblaze_core.models.enums import Modality as GBModality
    from genblaze_core.models.step import Step
    from genblaze_gmicloud import GMICloudImageProvider

    from remixkit.adapters import pricing

    provider = GMICloudImageProvider(models=pricing.priced_registry(GMICloudImageProvider))
    one = GBAsset(url="https://b2/a.png", media_type="image/png", sha256="a" * 64)

    for model in ("bria-genfill", "seededit-3-0-i2i-250628"):
        step = Step(provider="gmicloud-image", model=model, prompt="x",
                    modality=GBModality.IMAGE, params={})
        step.inputs = [one]
        assert provider.prepare_payload(step).get("image") == "https://b2/a.png", model
