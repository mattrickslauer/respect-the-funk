"""The identity builder — one surface, classed photographs, and a bounded prompt.

Three changes, one argument. An identity is what the generator is told about a person, and
all three failures it had were failures of *visibility*:

- It was editable from two screens. The artist page carried the builder and so did the
  builder page, both minting versions of one document, so two people could each overwrite
  the other from a form that was current when it rendered.
- Its reference frames were a pile. Thirty stills answered "how many" and hid the only
  question that matters — which views are covered — so a set could look well-stocked while
  documenting one angle twelve times.
- Its text was unbounded. Whatever somebody typed was prepended whole to every shot, and
  a long identity pushes the shot description far enough down the prompt that a backdrop
  loop renders as a portrait. That reads like the shot was ignored, because it was.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import (
    BodyBuild,
    FrameAngle,
    HeightBand,
    Identity,
    Presentation,
    ReferenceFrame,
)


def frame(name: str, angle: FrameAngle, lighting: str = "neutral") -> ReferenceFrame:
    return ReferenceFrame(
        key=f"remixkit/frames/{name}.png",
        angle=angle,
        lighting=lighting,
        sha256=f"{name:0>64}"[:64],
        content_type="image/png",
    )


def identity(**kwargs) -> Identity:
    return Identity(tenant_id="t", artist_id="art_1", **kwargs)


# ------------------------------------------------------------------ compression
def test_the_silhouette_comes_first_because_truncation_happens_last():
    """Clause order is salience order. Three enums outrank three paragraphs."""
    made = identity(
        presentation=Presentation.FEMININE,
        build=BodyBuild.ATHLETIC,
        height=HeightBand.TALL,
        structural_features="oval face, high cheekbones",
        wardrobe=["leather jacket"],
    )
    compiled = made.compile()

    assert compiled.startswith("feminine athletic tall build")
    assert compiled.index("oval face") < compiled.index("wearing leather jacket")


def test_unspecified_fields_contribute_nothing():
    """An artist who has not said should not have a guess sent on their behalf."""
    made = identity(structural_features="oval face")

    assert made.compile() == "oval face"
    assert "unspecified" not in made.compile()


def test_a_clause_over_budget_is_dropped_whole_never_cut_mid_sentence():
    """A prompt ending in "high cheekb" is worse than one that never mentioned it.

    A fragment is not less information — it is a different instruction, and the model
    will try to interpret it.
    """
    made = identity(
        build=BodyBuild.LEAN,
        structural_features="x" * 400,
        wardrobe=["leather jacket"],
    )
    compiled = made.compile()

    assert "x" * 400 not in compiled
    assert not compiled.endswith("x"), "a truncated clause reached the prompt"
    assert made.dropped_clauses >= 1


def test_a_later_clause_still_fits_when_an_earlier_one_is_refused():
    """Skipping is per clause, not a stop. The wardrobe survives an overlong face note."""
    made = identity(
        build=BodyBuild.LEAN,
        structural_features="x" * 400,
        wardrobe=["leather jacket"],
    )
    compiled = made.compile()

    assert compiled.startswith("lean")
    assert "wearing leather jacket" in compiled


def test_the_budget_is_actually_enforced():
    made = identity(
        presentation=Presentation.MASCULINE,
        build=BodyBuild.SOLID,
        height=HeightBand.TALL,
        structural_features="a face, " * 60,
        wardrobe=["a coat, " * 40],
    )
    assert len(made.compile()) <= Identity.PROMPT_BUDGET


def test_prompt_fragment_is_the_compiled_form():
    """The generator prepends `prompt_fragment`. If it bypassed the budget, the budget
    would be a decoration on a screen rather than a property of the run."""
    made = identity(build=BodyBuild.ATHLETIC, structural_features="oval face")

    assert made.prompt_fragment() == made.compile()


def test_a_fitting_identity_reports_nothing_dropped():
    made = identity(build=BodyBuild.ATHLETIC, structural_features="oval face",
                    wardrobe=["leather jacket"])
    assert made.dropped_clauses == 0


# ------------------------------------------------------------------ photo classes
def test_a_three_quarter_frame_outranks_a_front_one_for_the_single_slot():
    """A video shot gets one conditioning image, so which one is not a detail.

    Three-quarter carries the front planes *and* the depth of nose and cheekbone; a flat
    front-on frame is unambiguous about symmetry and says almost nothing about profile.
    Upload order must not decide this — a label adds frames in whatever order they were
    shot.
    """
    made = identity(reference_frames=[
        frame("a", FrameAngle.FRONT),
        frame("b", FrameAngle.PROFILE_LEFT),
        frame("c", FrameAngle.THREE_QUARTER_RIGHT),
    ])

    assert made.plates(limit=1)[0].angle is FrameAngle.THREE_QUARTER_RIGHT


def test_frames_are_deduplicated_per_class_and_lighting_together():
    """Two axes, because both change what a reference is: a face lit three ways is three
    references, and a face from three angles is three facts about one head."""
    made = identity(reference_frames=[
        frame("a", FrameAngle.FRONT, "neutral"),
        frame("b", FrameAngle.FRONT, "neutral"),
        frame("c", FrameAngle.FRONT, "warm"),
        frame("d", FrameAngle.BACK, "neutral"),
    ])

    chosen = made.plates(limit=10)
    assert len(chosen) == 3, "the second front/neutral frame is the same reference"


def test_coverage_lists_every_class_including_the_empty_ones():
    """A coverage view that hides what is missing is a progress bar that only counts up."""
    made = identity(reference_frames=[frame("a", FrameAngle.FRONT)])

    assert len(made.coverage) == len(list(FrameAngle))
    assert made.coverage[FrameAngle.FRONT]
    assert made.coverage[FrameAngle.BACK] == []
    assert made.covered_angles == 1


def test_a_frame_saved_before_the_classes_existed_reads_as_front():
    """Guessing anything else would retroactively mislabel a stored reference set."""
    assert ReferenceFrame(key="k").angle is FrameAngle.FRONT


# ------------------------------------------------------------------ the surfaces
@pytest.fixture
def artist(client):
    return client.post("/api/v1/artists", json={"name": "Amanda Kurt"}).json()


def test_the_identity_is_editable_from_exactly_one_place(client, artist):
    """It was editable from two, and saving mints a version — so two open screens could
    each mint a version over the other, from forms that were both current when drawn."""
    builder = client.get(f"/console/artists/{artist['id']}/identity").text
    page = client.get(f"/console/artists/{artist['id']}").text

    assert builder.count('name="structural_features"') == 1
    assert page.count('name="structural_features"') == 0
    assert f'href="/console/artists/{artist["id"]}/identity"' in page


def test_the_builder_shows_the_description_and_the_frames_together(client, artist):
    """They used to sit either side of a tab switch, which made "do the words and the
    pictures agree" the one question the layout prevented."""
    page = client.get(f"/console/artists/{artist['id']}/identity").text

    assert 'name="structural_features"' in page
    assert "What the model is shown" in page
    assert 'class="tab-state"' not in page, "the builder no longer splits itself in two"


def test_the_build_picker_offers_one_radio_per_build(client, artist):
    """Not one per build *per presentation*. The obvious construction loses the selection
    when presentation changes, because the checked input is in the set that vanished."""
    page = client.get(f"/console/artists/{artist['id']}/identity").text

    values = {"slight", "lean", "athletic", "solid", "full", "unspecified"}
    for value in values:
        assert page.count(f'name="build" value="{value}"') == 1


def test_the_upload_form_offers_exactly_the_classes_the_model_defines(
    client, container, principal, artist
):
    """Rendered from the enum, so adding a class cannot leave the form behind.

    Needs an identity to exist: frames attach to a version, so the upload form is only
    drawn once there is a version to attach them to.
    """
    container.identities.create_version(principal, artist["id"], structural_features="oval face")

    page = client.get(f"/console/artists/{artist['id']}/identity").text
    for angle in FrameAngle:
        assert f'value="{angle.value}"' in page


def test_saving_records_the_silhouette(client, container, principal, artist):
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"structural_features": "oval face", "presentation": "androgynous",
              "build": "lean", "height": "tall"},
    )
    stored = container.identities.current(principal, artist["id"])

    assert stored.presentation is Presentation.ANDROGYNOUS
    assert stored.build is BodyBuild.LEAN
    assert stored.height is HeightBand.TALL
    assert stored.compile().startswith("androgynous lean tall build")


def test_a_silhouette_can_be_un_set(client, container, principal, artist):
    """`UNSPECIFIED` is a real answer. Inheriting the previous value on an explicit clear
    would make these the only fields on the form that cannot be taken back."""
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"presentation": "feminine", "build": "full", "height": "short"},
    )
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"presentation": "unspecified", "build": "unspecified", "height": "unspecified"},
    )
    stored = container.identities.current(principal, artist["id"])

    assert stored.build is BodyBuild.UNSPECIFIED
    assert stored.compile() == ""


def test_an_omitted_silhouette_is_inherited_rather_than_wiped(client, container, principal, artist):
    """A save from a form that did not carry the field must not clear it."""
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"presentation": "masculine", "build": "solid"},
    )
    client.post(
        f"/ui/artists/{artist['id']}/identity", data={"structural_features": "square jaw"}
    )
    stored = container.identities.current(principal, artist["id"])

    assert stored.build is BodyBuild.SOLID
    assert stored.presentation is Presentation.MASCULINE


def test_an_uploaded_frame_records_its_class(client, container, principal, artist):
    identity_record = container.identities.create_version(
        principal, artist["id"], structural_features="oval face"
    )
    client.post(
        f"/ui/identities/{identity_record.id}/frames",
        files={"file": ("p.png", b"\x89PNGprofile", "image/png")},
        data={"angle": "profile-right", "lighting": "warm"},
    )
    stored = container.identities.current(principal, artist["id"])

    assert stored.reference_frames[-1].angle is FrameAngle.PROFILE_RIGHT


def test_an_unrecognised_class_falls_back_to_front(client, container, principal, artist):
    """Front is what every pre-classes frame actually is, so it is the honest default for
    a value the form did not offer — a refusal would lose an upload over a stale tab."""
    identity_record = container.identities.create_version(
        principal, artist["id"], structural_features="oval face"
    )
    response = client.post(
        f"/ui/identities/{identity_record.id}/frames",
        files={"file": ("p.png", b"\x89PNGweird", "image/png")},
        data={"angle": "from-below", "lighting": "neutral"},
    )
    assert response.status_code == 200

    stored = container.identities.current(principal, artist["id"])
    assert stored.reference_frames[-1].angle is FrameAngle.FRONT


# ------------------------------------------------------------------ full CRUD
def test_a_reference_frame_can_be_removed(client, container, principal, artist):
    """It could be added and never removed, so a mis-classified upload was permanent —
    and a profile filed as a front kept winning the one conditioning slot forever."""
    record = container.identities.create_version(principal, artist["id"], structural_features="x")
    for angle in ("three-quarter-left", "front", "profile-right"):
        client.post(
            f"/ui/identities/{record.id}/frames",
            files={"file": (f"{angle}.png", b"\x89PNG" + angle.encode(), "image/png")},
            data={"angle": angle, "lighting": "neutral"},
        )

    response = client.delete(f"/ui/identities/{record.id}/frames/1")
    assert response.status_code == 200

    stored = container.identities.current(principal, artist["id"])
    assert [f.angle.value for f in stored.reference_frames] == [
        "three-quarter-left", "profile-right"
    ], "removal is by position, and it took the one at that position"


def test_removing_a_frame_takes_its_object_with_it(client, container, principal, artist):
    """An orphaned still is a bucket paying for something no screen can reach."""
    record = container.identities.create_version(principal, artist["id"], structural_features="x")
    client.post(
        f"/ui/identities/{record.id}/frames",
        files={"file": ("f.png", b"\x89PNGone", "image/png")},
        data={"angle": "front", "lighting": "neutral"},
    )
    key = container.identities.current(principal, artist["id"]).reference_frames[0].key
    assert container.storage.exists(key)

    client.delete(f"/ui/identities/{record.id}/frames/0")
    assert not container.storage.exists(key)


def test_one_photograph_filed_under_two_classes_survives_deleting_one(
    client, container, principal, artist
):
    """The object key is derived from the content digest alone, so the same still filed
    as `front` and as `profile-left` is two rows sharing one key. Deleting the object on
    the first removal would break the image the second row still renders."""
    record = container.identities.create_version(principal, artist["id"], structural_features="x")
    for angle in ("front", "profile-left"):
        client.post(
            f"/ui/identities/{record.id}/frames",
            files={"file": ("same.png", b"\x89PNGidentical", "image/png")},
            data={"angle": angle, "lighting": "neutral"},
        )
    stored = container.identities.current(principal, artist["id"])
    assert len({f.key for f in stored.reference_frames}) == 1, "the fixture must share a key"
    key = stored.reference_frames[0].key

    client.delete(f"/ui/identities/{record.id}/frames/0")

    assert container.storage.exists(key), "the surviving row's image was deleted"


def test_restoring_copies_a_version_forward_rather_than_rewinding(
    client, container, principal, artist
):
    """A version rewound in place leaves a kit generated last week pointing at a version
    number whose content has since changed underneath it — the exact failure versioning
    exists to prevent. History only grows, and going back is itself an event."""
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"structural_features": "oval face", "build": "athletic"},
    )
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={"structural_features": "square jaw", "build": "solid"},
    )
    first = next(
        i for i in container.identities.list_for_artist(principal, artist["id"]) if i.version == 1
    )

    assert client.post(f"/ui/identities/{first.id}/restore").status_code == 200

    versions = container.identities.list_for_artist(principal, artist["id"])
    assert [v.version for v in versions] == [3, 2, 1], "nothing was erased"
    current = container.identities.current(principal, artist["id"])
    assert current.build is BodyBuild.ATHLETIC
    assert "oval face" in current.compile()


def test_deleting_a_version_keeps_frames_another_version_still_uses(
    client, container, principal, artist
):
    """`restore_version` copies frames forward by reference — two versions naming one
    immutable still. Deleting a version's objects unconditionally would blank the frames
    of the version it was restored from. Nothing errors; the rows stay and the images
    404, which is the shape of data loss that is hardest to notice.
    """
    record = container.identities.create_version(principal, artist["id"], structural_features="x")
    client.post(
        f"/ui/identities/{record.id}/frames",
        files={"file": ("f.png", b"\x89PNGshared", "image/png")},
        data={"angle": "front", "lighting": "neutral"},
    )
    original = container.identities.current(principal, artist["id"])
    key = original.reference_frames[0].key

    restored = container.identities.restore_version(principal, original.id)
    assert restored.reference_frames[0].key == key, "the fixture must share the object"

    container.identities.delete(principal, restored.id)

    assert container.storage.exists(key), "the original version's image was deleted"
    assert container.identities.current(principal, artist["id"]).reference_frames


def test_an_identity_can_be_approved(client, container, principal, artist):
    """`set_approval` had been on the service with nothing able to call it, so every
    identity in the console was permanently a draft."""
    record = container.identities.create_version(principal, artist["id"], structural_features="x")

    assert client.post(
        f"/ui/identities/{record.id}/approval", data={"state": "approved"}
    ).status_code == 200
    assert container.identities.get(principal, record.id).approval.value == "approved"


def test_a_bogus_approval_state_is_refused(client, container, principal, artist):
    record = container.identities.create_version(principal, artist["id"], structural_features="x")

    response = client.post(f"/ui/identities/{record.id}/approval", data={"state": "shipped"})
    assert response.status_code == 400
    assert container.identities.get(principal, record.id).approval.value == "draft"


def test_removing_a_frame_that_is_not_there_is_a_404(client, container, principal, artist):
    record = container.identities.create_version(principal, artist["id"], structural_features="x")
    assert client.delete(f"/ui/identities/{record.id}/frames/7").status_code == 404


# ------------------------------------------------------------------ the layers
def test_the_builder_is_three_columns_and_the_work_is_layered(client, container, principal, artist):
    """The page has three jobs on screen at once — told, shown, said before — and they
    are only useful together. Depth carries what would otherwise be a fourth column."""
    container.identities.create_version(principal, artist["id"], structural_features="x")
    page = client.get(f"/console/artists/{artist['id']}/identity").text

    assert "deck-3" in page
    for layer in ("add-frame", "history", "lightbox"):
        assert f'id="{layer}"' in page, f"the {layer} surface is missing"
    assert 'class="pinned"' in page, "the compiled prompt must not scroll away while editing"


def test_nothing_reaches_for_a_bare_z_index():
    """Every layer is named on the scale in `:root`. A bare integer is the start of the
    stacking arms race the scale exists to prevent."""
    import re
    from pathlib import Path

    css = Path("remixkit/ui/static/console.css").read_text()
    body = css.split("}", 1)[1]  # everything after the :root token block
    bare = re.findall(r"z-index:\s*(?!var\()([^;}]+)", body)
    assert not bare, f"z-index values not on the scale: {bare}"
