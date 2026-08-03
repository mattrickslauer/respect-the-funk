"""Formats as data, and the lineage of what they produce.

Everything this app could generate used to be one hardcoded opinion in `services/briefs`:
a templatable backdrop loop, four fixed moods, `clean centre frame left empty for a
subject` welded into the prompt and `speech` in the negatives. Good description of one
product, poor description of a video tool — and the gap showed the first time somebody
asked for their artist *talking to camera*, because the pipeline would have spent real
money instructing a model not to produce speech.

These tests hold the two properties that make formats data rather than code: a format
carries its own prompt, negatives, length policy and face policy; and the asset it
produces records what it was made from.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import (
    FacePolicy,
    FrameAngle,
    Modality,
    Recipe,
    ReferenceFrame,
)
from remixkit.ports.generator import GenerationRequest
from remixkit.services.briefs import plan_from_recipe
from remixkit.services.recipes import builtin_recipes

LINE = "Hope to see you at my show this August 15th at the Hollywood Bowl"


def recipe(slug: str) -> Recipe:
    return next(r for r in builtin_recipes() if r.slug == slug)


@pytest.fixture
def artist(container, principal):
    made = container.artists.create(principal, name="Amanda Kurt")
    container.artists.set_consent(principal, made.id, granted=True, signed_by="m@l.example")
    return container.artists.get(principal, made.id)


@pytest.fixture
def song(container, principal, artist):
    made = container.songs.create(principal, artist.id, title="Un Poquito Mas")
    return container.songs.set_measurement(principal, made.id, bpm=96.0, bpm_method="manual")


@pytest.fixture
def identity(container, principal, artist):
    """Amanda's real set: front and three-quarter, so a format's choice is observable."""
    container.identities.create_version(
        principal, artist.id, presentation="feminine", build="slight"
    )
    current = container.identities.current(principal, artist.id)
    for angle in (FrameAngle.FRONT, FrameAngle.THREE_QUARTER_LEFT):
        key = f"remixkit/f/{angle.value}.png"
        container.storage.put(key, b"png", content_type="image/png")
        container.identities.add_reference_frame(
            principal,
            current.id,
            ReferenceFrame(
                key=key, angle=angle, sha256=angle.value.ljust(64, "0")[:64],
                content_type="image/png",
            ),
        )
    return container.identities.current(principal, artist.id)


def resolve(container, identity, song, shots):
    request = GenerationRequest(
        kit_id="preview", tenant_id="t", artist_name="Amanda Kurt",
        identity=identity, song=song, shots=shots,
    )
    return [p for p, _ in container.generator._resolve(request)]


# ------------------------------------------------------------------ templates
def test_a_clause_whose_field_is_empty_is_dropped_whole():
    """A template is written for the richest case and most assets are not that.

    Rendering `. . ` where a tempo should have been is how a prompt acquires punctuation a
    model tries to interpret.
    """
    made = Recipe(tenant_id="t", slug="x", name="X",
                  prompt_template="A shot of {artist}. {tempo}. Filmed on location")

    rendered = made.render(artist="Amanda Kurt", tempo="")

    assert rendered == "A shot of Amanda Kurt. Filmed on location"
    assert ".." not in rendered


def test_a_placeholder_the_caller_does_not_supply_drops_its_clause():
    """Not an error — a clause that does not apply to this asset."""
    made = Recipe(tenant_id="t", slug="x", name="X",
                  prompt_template="A shot of {artist}. She says: {line}")

    assert made.render(artist="Amanda") == "A shot of Amanda"


# ------------------------------------------------------------------ the formats
def test_a_spoken_format_does_not_negative_speech(container, identity, song):
    """The defect this whole change exists for. One negatives list served every shot, and it
    carried `speech` and `singing` because they stopped backdrops scoring themselves — a
    talking clip run through that is a provider being paid to defeat the brief."""
    shots = plan_from_recipe(recipe("direct-address"), song, count=1,
                             artist_name="Amanda Kurt", line=LINE)
    step = resolve(container, identity, song, shots)[-1]

    assert "speech" not in step.negative_prompt
    assert "singing" not in step.negative_prompt
    assert LINE in step.prompt


def test_a_backdrop_still_refuses_to_score_itself(container, identity, song):
    shots = plan_from_recipe(recipe("backdrop-loop"), song, count=1, artist_name="Amanda Kurt")
    step = resolve(container, identity, song, shots)[-1]

    for term in ("music", "soundtrack", "speech"):
        assert term in step.negative_prompt


def test_a_backdrop_is_not_told_what_the_artist_looks_like(container, identity, song):
    """`FacePolicy.NONE` is not "no picture of them" — it is "this asset is not about
    them". A frame with a deliberately empty centre does not become more templatable for
    carrying a description of a face."""
    shots = plan_from_recipe(recipe("backdrop-loop"), song, count=1, artist_name="Amanda Kurt")
    step = resolve(container, identity, song, shots)[-1]

    assert "feminine slight" not in step.prompt
    assert step.reference_keys == []
    assert step.prelude is None


def test_a_selfie_asks_for_the_front_frame(container, identity, song):
    """The bug this fixes: three-quarter ranked first *everywhere*, so a phone held at
    arm's length — which is front-on — was handed the wrong class because the ranking knew
    nothing about the framing it served."""
    shots = plan_from_recipe(recipe("direct-address"), song, count=1,
                             artist_name="Amanda Kurt", line=LINE)
    step = resolve(container, identity, song, shots)[-1]

    assert step.prelude.reference_keys == ["remixkit/f/front.png"]


def test_a_cinematic_format_still_asks_for_three_quarter(container, identity, song):
    """The global order is not wrong, it was wrong as a *global*. A cinematic frame is
    rarely flat-on and the class carries both the front planes and the depth."""
    shots = plan_from_recipe(recipe("performance"), song, count=1, artist_name="Amanda Kurt")
    step = resolve(container, identity, song, shots)[-1]

    assert step.prelude.reference_keys == ["remixkit/f/three-quarter-left.png"]


def test_a_spoken_format_ignores_the_bar_grid(container, song):
    """A sentence takes as long as it takes. Clipping speech to a musical window cuts a
    word in half, which is worse than ignoring the grid."""
    spoken = plan_from_recipe(recipe("direct-address"), song, count=1, line=LINE)[0]
    loop = plan_from_recipe(recipe("backdrop-loop"), song, count=1)[0]

    assert spoken.seconds == 8.0
    assert loop.seconds != 8.0, "a loop follows its hook window"


def test_the_variant_spread_belongs_to_the_format(container, song):
    """The planner used to apply one table of four moods to everything. A selfie in a
    kitchen and a performance on an empty stage do not share a spread."""
    address = {s.prompt for s in plan_from_recipe(recipe("direct-address"), song, count=2, line=LINE)}
    stage = {s.prompt for s in plan_from_recipe(recipe("performance"), song, count=2)}

    assert len(address) == 2 and len(stage) == 2
    assert not (address & stage)


def test_an_image_format_plans_an_image(container, identity, song):
    shots = plan_from_recipe(recipe("portrait-still"), song, count=1, artist_name="Amanda Kurt")
    step = resolve(container, identity, song, shots)[-1]

    assert step.modality is Modality.IMAGE
    assert step.rendered_seconds is None


def test_every_builtin_that_locks_a_face_names_the_class_it_wants():
    """A format that locks a face and states no angle is leaving the most consequential
    choice to a global default that cannot know its framing."""
    for made in builtin_recipes():
        if made.face in (FacePolicy.PLATE, FacePolicy.LOCKED):
            assert made.face_angle is not None, f"{made.slug} locks a face and picks no class"


# ------------------------------------------------------------------ provenance
def test_an_asset_records_what_it_was_made_from(container, principal, identity, song):
    """"Which base did this come out of" should be readable off the asset rather than
    reconstructed from a kit brief that may since have been edited or deleted."""
    shots = plan_from_recipe(recipe("direct-address"), song, count=1,
                             artist_name="Amanda Kurt", line=LINE)
    result = container.generator.generate(
        GenerationRequest(kit_id="k", tenant_id=principal.tenant_id,
                          artist_name="Amanda Kurt", identity=identity, song=song, shots=shots)
    )
    assert not result.error, result.error

    still = next(a for a in result.assets if a.modality is Modality.IMAGE)
    clip = next(a for a in result.assets if a.modality is Modality.VIDEO)

    assert still.recipe_slug == "direct-address"
    assert still.reference_keys == ["remixkit/f/front.png"]
    assert [r["label"] for r in still.identity_refs] == ["Primary"]
    assert [r["version"] for r in still.identity_refs] == [1]


def test_the_clip_records_the_still_it_was_generated_from(container, principal, identity, song):
    """The link most worth having — it names the frame the likeness came from. It can only
    be made after the run, because the still's object key is assigned by the sink."""
    shots = plan_from_recipe(recipe("direct-address"), song, count=1,
                             artist_name="Amanda Kurt", line=LINE)
    result = container.generator.generate(
        GenerationRequest(kit_id="k", tenant_id=principal.tenant_id,
                          artist_name="Amanda Kurt", identity=identity, song=song, shots=shots)
    )

    still = next(a for a in result.assets if a.modality is Modality.IMAGE)
    clip = next(a for a in result.assets if a.modality is Modality.VIDEO)

    assert clip.derived_from == [still.key]


def test_a_backdrop_records_no_identity(container, principal, identity, song):
    """Provenance has to be able to say *nobody*, or "we do not know" and "no one" read
    the same on a delivered file."""
    shots = plan_from_recipe(recipe("backdrop-loop"), song, count=1, artist_name="Amanda Kurt")
    result = container.generator.generate(
        GenerationRequest(kit_id="k", tenant_id=principal.tenant_id,
                          artist_name="Amanda Kurt", identity=identity, song=song, shots=shots)
    )

    clip = result.assets[0]
    assert clip.recipe_slug == "backdrop-loop"
    assert clip.reference_keys == []
    assert clip.derived_from == []


# ------------------------------------------------------------------ wired through
def test_the_builtins_are_seeded_on_first_read(container, principal):
    """No migration step exists to hang a fixture on — a tenant is created by the first
    request that mentions it, so the first read writes the floor."""
    assert container.repo.list(principal.tenant_id, "recipes", Recipe) == []

    seeded = container.recipes.list(principal)

    assert {r.slug for r in seeded} >= {"backdrop-loop", "direct-address", "performance"}
    assert all(r.builtin for r in seeded)


def test_seeding_happens_once(container, principal):
    """An operator who has edited the built-ins is never overwritten by a later read."""
    first = container.recipes.list(principal)
    first[0].name = "Edited"
    container.recipes.save(principal, first[0])

    assert any(r.name == "Edited" for r in container.recipes.list(principal))
    assert len(container.recipes.list(principal)) == len(first)


def test_a_builtin_refuses_to_be_deleted(container, principal):
    from remixkit.services.errors import Conflict

    with pytest.raises(Conflict):
        container.recipes.delete(principal, "performance")


def test_the_default_format_carries_the_face(container, principal, identity, song):
    """`backdrop-loop` was the only format and is the wrong default now there is a choice:
    it deliberately has nobody in it, so defaulting to it would mean an artist's identity
    reached nothing unless somebody went looking for the right format."""
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)

    assert plan.shots[0].prelude is not None, "the default must condition on the artist"


def test_a_kit_records_and_replays_its_format(container, principal, identity, song):
    """The brief names the format by slug, and the worker replans from it — otherwise a
    kit queued as a selfie runs as whatever the default happens to be."""
    kit = container.kits.request(
        principal, song_id=song.id, video_count=1,
        recipe_slug="direct-address", line=LINE,
    )
    assert kit.brief["recipe_slug"] == "direct-address"
    assert kit.brief["line"] == LINE

    ran = container.kits.run(principal.tenant_id, kit.id)
    assert ran.status.value == "ready", ran.error
    clip = next(a for a in ran.assets if a.modality is Modality.VIDEO)
    assert clip.recipe_slug == "direct-address"
    assert LINE in clip.prompt


def test_the_generate_screen_offers_the_formats(client, container, principal, artist, song):
    page = client.get(
        f"/console/artists/{artist.id}/songs/{song.id}/generate"
    ).text

    for slug in ("backdrop-loop", "direct-address", "performance"):
        assert f'name="recipe_slug" value="{slug}"' in page


def test_the_line_field_appears_only_for_a_format_that_speaks(client, artist, song):
    """A field that changes nothing is worse than an absent one — it looks like it did."""
    base = f"/console/artists/{artist.id}/songs/{song.id}/generate"

    assert 'name="line"' not in client.get(f"{base}?recipe_slug=backdrop-loop").text
    assert 'name="line"' in client.get(f"{base}?recipe_slug=direct-address").text


def test_the_format_rides_the_queue_button(client, artist, song):
    """It decides more than any other control on the page, so buying a different format
    than the one priced is the sharpest version of the divergence this screen prevents."""
    page = client.get(
        f"/console/artists/{artist.id}/songs/{song.id}/generate?recipe_slug=direct-address"
    ).text
    queue_form = page.split('hx-post="/ui/kits"', 1)[1].split("</form>", 1)[0]
    priced = page.split('id="repricer"', 1)[1].split("</form>", 1)[0]

    # The button submits the priced form rather than a copy of it, so the format rides on
    # the picker itself — the same element the plan below was built from.
    assert 'hx-include="#repricer"' in queue_form
    picked = priced.split('value="direct-address"', 1)[1].split(">", 1)[0]
    assert "checked" in picked, "the priced format is not the one the picker shows"


def test_a_spoken_line_rides_alongside_as_its_own_format(container, principal, identity, song):
    """`tts_text` was a branch in the video planner. It is a recipe now, so it gets the
    same prompt, provenance and preview treatment as everything else."""
    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, tts_text="one spoken line"
    )

    audio = [s for s in plan.shots if s.modality is Modality.AUDIO]
    assert len(audio) == 1
    assert audio[0].prompt == "one spoken line", "a TTS model is asked to say it, not to read a brief"
