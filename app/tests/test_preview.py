"""The dry run — what the screen promises about a run it has not bought.

Every test here defends one claim: **the preview is the run**. The incident these were
written after is worth stating plainly, because it is the shape of every failure below.

On 2026-07-31 a kit was generated for "Un Poquito Más". What came back was a stock night
drive scored with a lofi instrumental the model composed itself. Nothing had crashed. The
plan screen showed a row reading `Vertical 9:16 loop, moody night drive…` and that row was
not the prompt — it was the mood fragment, before composition, truncated to ninety
characters. The string that reached the provider named no song, no artist, and no
constraint against audio, and there was no screen on which that could be seen.

So: the composed prompt is asserted to be identical to the one the generator sends, a
rewritten prompt is asserted to survive all the way into the worker, and a video shot is
asserted to carry the negatives that tell a model not to score its own footage.
"""

from __future__ import annotations

import html
import re

import pytest

from remixkit.domain.models import Modality
from remixkit.ports.generator import GenerationRequest


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


# ------------------------------------------------------------------ one path
def test_the_previewed_prompt_is_the_prompt_that_would_be_sent(container, principal, song, artist):
    """The claim the whole screen rests on, asserted against the generator itself.

    Not "the preview looks similar to the run" — identical, field by field, because they
    are produced by one call. A second implementation that agreed today would be free to
    drift tomorrow, and the drift would be invisible until an invoice arrived.
    """
    plan, shots = container.kits.plan(principal, song_id=song.id, video_count=2)

    sent = container.generator._resolve(
        GenerationRequest(
            kit_id="whatever",
            tenant_id=principal.tenant_id,
            artist_name=artist.name,
            identity=container.identities.current(principal, artist.id),
            song=song,
            shots=shots,
        )
    )

    assert [s.prompt for s in plan.shots] == [s.prompt for s, _ in sent]
    assert [s.model for s in plan.shots] == [s.model for s, _ in sent]
    assert [s.params for s in plan.shots] == [s.params for s, _ in sent]
    assert [s.rendered_seconds for s in plan.shots] == [s.rendered_seconds for s, _ in sent]


def test_the_preview_spends_nothing(container, principal, song):
    """A dry run must not enqueue, must not write a kit, and must not call a provider."""
    before = len(container.kits.list(principal))

    container.kits.plan(principal, song_id=song.id, video_count=3)

    assert container.kits.list(principal) == [] or len(container.kits.list(principal)) == before
    assert container.enqueued == [], "planning is not queueing"


def test_the_composed_prompt_names_the_release(container, principal, song):
    """The defect itself. Every video prompt used to be one of four fixed mood strings.

    Naming the release does not, on its own, teach a model a song it has never heard. It
    is asserted because a prompt that mentions nothing about the record is one nobody
    reading the plan can tell apart from another artist's — which is how four identical
    stock clips got bought without anyone noticing they were generic.
    """
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=2)
    videos = [s for s in plan.shots if s.modality is Modality.VIDEO]

    assert videos
    for step in videos:
        assert "Un Poquito Mas" in step.prompt
        assert "Amanda Kurt" in step.prompt
        assert "96 BPM" in step.prompt, "the measured tempo, not an invented genre"


def test_a_video_shot_is_told_not_to_score_itself(container, principal, song):
    """Veo, Sora 2 and Seedance all return a native audio track unless told otherwise.

    This format's picture sits under the master, so audio in it is a defect. This is a
    conditioning signal rather than a mute switch — see `recipes.SILENT_NEGATIVES` — and
    the assertion is only that the signal is actually sent, which it previously was not.
    """
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    # The *audio* terms, on every format whose asset sits under a master. "singing" is
    # deliberately not among them here: it is a defect in a backdrop and the subject of a
    # performance clip, which is exactly the kind of thing that had to stop being global.
    for term in ("music", "soundtrack", "speech"):
        assert term in video.negative_prompt


def test_an_unmeasured_song_contributes_no_tempo_rather_than_a_guess(container, principal, artist):
    """The provenance rule, applied to a prompt: no measurement, no claim."""
    unmeasured = container.songs.create(principal, artist.id, title="Nothing Measured")

    plan, _ = container.kits.plan(principal, song_id=unmeasured.id, video_count=1)
    video = next(s for s in plan.shots if s.modality is Modality.VIDEO)

    assert "BPM" not in video.prompt


# ------------------------------------------------------------------ editing
def test_a_rewritten_prompt_is_what_gets_generated(container, principal, song):
    """An edit made on the preview must survive the queue and reach the provider.

    The path is long — form, service, `Kit.brief`, JSON, the worker, the generator — and
    it crosses a serialisation boundary that has no integer keys. Asserting at the far end
    is the only assertion that covers all of it.
    """
    mine = "Static wide shot of an empty Miami balcony at dusk, no camera movement"

    kit = container.kits.request(
        principal, song_id=song.id, video_count=1, overrides={0: {"prompt": mine}}
    )
    assert kit.brief["shot_overrides"] == [{"index": 0, "prompt": mine}]

    ran = container.kits.run(principal.tenant_id, kit.id)
    assert ran.assets, ran.error
    assert ran.assets[0].prompt == mine, "the worker replanned and discarded the edit"


def test_a_cleared_field_restores_the_generated_default(container, principal, song):
    """Empty is not an override. A blank box means "use the default", not "send nothing"."""
    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, overrides={0: {"prompt": "   "}}
    )
    assert "Un Poquito Mas" in plan.shots[0].prompt


def test_an_override_for_a_shot_that_no_longer_exists_is_dropped(container, principal, song):
    """Never applied to a neighbour. Losing an edit is visible; moving one is not."""
    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, overrides={7: {"prompt": "orphan"}}
    )
    assert all("orphan" not in step.prompt for step in plan.shots)


def test_a_pinned_model_is_priced_at_that_model(container, principal, song):
    """Editing the model has to move the estimate, or the field is decoration."""
    default_plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    pinned, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, overrides={0: {"model": "sora-2"}}
    )

    assert pinned.shots[0].model == "sora-2"
    assert pinned.estimate_cents != default_plan.estimate_cents


# ------------------------------------------------------------------ refusals
def test_a_run_with_no_provider_is_refused_before_the_button(container, principal, song, monkeypatch):
    """A modality nothing can serve is a fact worth knowing while refusing is free."""
    from remixkit.adapters import providers as provider_table

    monkeypatch.setattr(provider_table, "resolve", lambda backend: {})

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=2)

    assert plan.blocker
    assert plan.runnable == []
    assert plan.estimate_cents == 0, "a run that cannot happen must not be quoted"


def test_a_skipped_shot_is_shown_and_not_priced(container, principal, song, monkeypatch):
    """A modality nothing can serve costs nothing, produces nothing, and says why."""
    from remixkit.adapters import providers as provider_table

    full = provider_table.resolve("mock")
    monkeypatch.setattr(
        provider_table,
        "resolve",
        # Video and image, no audio — so the voice shot has nothing to run on.
        lambda backend: {m: full[m] for m in (Modality.VIDEO, Modality.IMAGE)},
    )

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, tts_text="a spoken line"
    )

    assert len(plan.runnable) == 1
    assert len(plan.skipped) == 1
    assert plan.skipped[0].modality is Modality.AUDIO
    assert "audio" in plan.skipped[0].skipped_reason
    # The quote is the runnable shot and nothing else. This artist has no reference
    # frames, so there is no identity-locking still to pay for either.
    runnable = plan.runnable[0]
    assert runnable.prelude is None
    assert plan.estimate_cents == runnable.estimate_cents


def test_consent_blocks_the_plan_not_just_the_queue(container, principal):
    """The refusal `KitService.request` makes, surfaced on the screen that precedes it."""
    unconsented = container.artists.create(principal, name="No Release")
    song = container.songs.create(principal, unconsented.id, title="Blocked")

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)

    assert plan.blocker and "likeness consent" in plan.blocker


# ------------------------------------------------------------------ the screen
def test_the_screen_shows_the_whole_prompt_not_a_truncation(client, container, principal):
    """The specific thing that hid the defect: a 90-character `truncate` on the plan.

    The prompt is long, and showing all of it is the point — the clause that stops the
    model writing its own soundtrack sits at the end of the string, which is precisely
    where a truncation removes it from view.
    """
    made = container.artists.create(principal, name="Amanda Kurt")
    container.artists.set_consent(principal, made.id, granted=True, signed_by="m@l.example")
    song = container.songs.create(principal, made.id, title="Un Poquito Mas")

    # Unescaped before comparing: the prompt quotes the title, and Jinja renders `"` as
    # `&#34;` inside the textarea. That is correct output and a false negative here.
    page = html.unescape(
        client.get(
            f"/console/artists/{made.id}/songs/{song.id}/generate?video_count=1"
        ).text
    )

    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1)
    assert plan.shots[0].prompt in page, "the plan on the page must be the plan on the wire"
    for term in plan.shots[0].negative_prompt.split(", ")[:3]:
        assert term in page, "what the run suppresses is part of what it is about to buy"


# ------------------------------------------------------------------ the controls
@pytest.fixture
def two_faces(client, container, principal, artist):
    """An artist with a primary line and a second member, both with a reference frame."""
    from remixkit.domain.models import FrameAngle, ReferenceFrame

    for name in ("", "Marco"):
        client.post(
            f"/ui/artists/{artist.id}/identity",
            data={"name": name, "presentation": "feminine", "build": "slight"},
        )
        identity = container.identities.current(principal, artist.id, name)
        key = f"remixkit/f/{name or 'p'}.png"
        container.storage.put(key, b"png", content_type="image/png")
        container.identities.add_reference_frame(
            principal,
            identity.id,
            ReferenceFrame(
                key=key, angle=FrameAngle.FRONT,
                sha256=(name or "p").ljust(64, "0")[:64], content_type="image/png",
            ),
        )
    return artist


def test_the_generate_screen_offers_every_face(client, song, two_faces):
    """`identity_names` reached the service with no control on any screen, so the console
    always generated the primary line however many faces an artist had."""
    page = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate"
    ).text

    assert page.count('name="identity_names" value=') == 2
    assert 'name="faces_chosen"' in page


def test_the_primary_is_ticked_before_anything_is_chosen(client, song, two_faces):
    """The boxes must describe the run below them. Nothing ticked while the plan uses the
    primary would be the screen disagreeing with itself."""
    page = client.get(f"/console/artists/{two_faces.id}/songs/{song.id}/generate").text

    assert re.search(r'name="identity_names" value=""[^>]*checked', page)


def test_choosing_two_faces_puts_both_in_the_prompt(client, song, two_faces):
    page = html.unescape(
        client.get(
            f"/console/artists/{two_faces.id}/songs/{song.id}/generate"
            "?video_count=1&faces_chosen=1&identity_names=&identity_names=Marco"
        ).text
    )

    assert "Amanda Kurt: feminine slight" in page
    assert "Marco: feminine slight" in page or "Marco:" in page


def test_a_kit_with_no_face_is_reachable(client, container, principal, song, two_faces):
    """Clearing every box is a real answer — a backdrop with nobody in it — and it has to
    be distinguishable from not having chosen. The empty string cannot carry that, because
    it is the primary line's actual name."""
    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "1",
              "faces_chosen": "1"},
    )
    kit = container.kits.list(principal)[0]

    assert kit.brief["identities"] == []


def test_a_shot_length_can_be_edited(client, container, principal, song, two_faces):
    """`seconds` was overridable from the day per-shot editing landed and had no field —
    the one thing a loop is measured in was the one thing the plan could not change."""
    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "1",
              "seconds_0": "4.5"},
    )
    kit = container.kits.list(principal)[0]
    ran = container.kits.run(principal.tenant_id, kit.id)

    clip = next(a for a in ran.assets if a.modality is Modality.VIDEO)
    assert clip.params["duration"] == 4


def test_the_face_can_be_turned_off_for_one_shot(client, container, principal, song, two_faces):
    """A backdrop that wants nobody in it should not pay for a still that locks a face."""
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=2)
    assert all(s.prelude for s in plan.shots), "both loops lock by default"

    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "2",
              "identity_lock_1": "off"},
    )
    kit = container.kits.list(principal)[0]
    ran = container.kits.run(principal.tenant_id, kit.id)

    stills = [a for a in ran.assets if a.modality is Modality.IMAGE]
    assert len(stills) == 1, "the second loop rendered a still it was told not to"


def test_an_estimate_over_the_ceiling_refuses_before_the_button(client, song, two_faces):
    """`KitService.request` raises on this. A button that submits into a known refusal is
    a round trip to be told something the screen already knew."""
    page = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate?video_count=3&budget_cents=10"
    ).text

    assert "is over the" in page
    assert re.search(r"Queue this kit", page)
    assert "disabled" in page


def test_the_kit_can_be_named(client, container, principal, song, two_faces):
    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "1",
              "name": "Duo test"},
    )
    assert container.kits.list(principal)[0].name == "Duo test"


def test_every_control_rides_the_queue_button(client, song, two_faces):
    """A control that changes the plan but does not travel with the purchase buys a
    different kit than the one priced — the divergence this screen exists to prevent.

    The controls used to be copied into the queue form as hidden inputs, rendered from the
    same context as the estimate. That was only true of the screen that was last loaded:
    once the plan re-prices itself in place, the copies are of an older plan than the one
    on display. So the button submits the priced form itself, and this asserts the two
    halves of that — that it says so, and that the form it names holds every control.
    """
    page = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate"
        "?video_count=1&faces_chosen=1&identity_names=Marco&tts_text=hello&budget_cents=900"
    ).text
    queue_form = page.split('hx-post="/ui/kits"', 1)[1].split("</form>", 1)[0]
    priced = page.split('id="repricer"', 1)[1].split("</form>", 1)[0]

    assert 'hx-include="#repricer"' in queue_form
    for control in ("recipe_slug", "video_count", "faces_chosen", "identity_names",
                    "tts_text", "budget_cents"):
        assert f'name="{control}"' in priced, f"{control} does not travel with the purchase"
    # The per-shot edits live in the step cards and join the form by `form="repricer"`,
    # which puts them in its FormData without putting them in its subtree.
    assert 'form="repricer"' in page.split('id="repricer"', 1)[1]


def test_the_queue_button_carries_no_second_copy_of_the_plan(client, song, two_faces):
    """The regression this shape exists to make impossible. A hidden copy of a control is
    right until the plan is re-priced without a reload, and then it is a purchase of the
    screen somebody saw a minute ago."""
    page = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate?video_count=1&tts_text=hello"
    ).text
    queue_form = page.split('hx-post="/ui/kits"', 1)[1].split("</form>", 1)[0]

    for copied in ("video_count", "recipe_slug", "tts_text", "budget_cents",
                   "section_ids", "identity_names"):
        assert f'name="{copied}"' not in queue_form, f"{copied} is duplicated onto the button"


def test_the_shot_count_survives_being_retyped(client, song, two_faces):
    """Clearing "3" to type "4" leaves the field empty between two keystrokes, and the
    plan now re-prices on every one of them. An `int` parameter answers that with a 422,
    which live pricing shows as the estimate quietly ceasing to move."""
    response = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate?video_count="
    )

    assert response.status_code == 200
    field = response.text.split('name="video_count"', 1)[1].split(">", 1)[0]
    assert 'value=""' in field, "a box being retyped was refilled with a number nobody typed"


def test_repricing_with_an_empty_budget_field_works(client, song, two_faces):
    """The reported bug. A blank `<input type="number">` submits as the empty string, and
    an `int | None` parameter rejects that with a 422 — so the budget ceiling, whose whole
    design is that leaving it blank means "no ceiling", broke Re-price for anyone who did
    not set one.
    """
    response = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate"
        "?video_count=3&budget_cents=&tts_text="
    )

    assert response.status_code == 200
    assert "is over the" not in response.text, "no ceiling means nothing to be over"


@pytest.mark.parametrize("value", ["", "abc", "-5", "12.7", "500"])
def test_the_budget_field_never_refuses_to_render_the_page(client, song, two_faces, value):
    """It is a spend guard. Refusing the page because somebody typed "abc" into it would
    hide the estimate they were opening the page to check."""
    response = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate?video_count=1"
        f"&budget_cents={value}"
    )
    assert response.status_code == 200


def test_queueing_with_a_blank_ceiling_is_not_a_refusal(client, container, principal, song, two_faces):
    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "1",
              "budget_cents": ""},
    )
    assert len(container.kits.list(principal)) == 1


# ------------------------------------------------------------ the prompt box
#
# The box is prefilled with the composed prompt, because a preview you cannot correct is
# one that only tells you afterwards. That made every submission carry a prompt, and every
# submission therefore look like a rewrite — so the plan stopped tracking the brief the
# moment the screen re-priced once, which it does on every keystroke.
#
# Two things followed, and both reached a provider. The identity fragment is *shown* in the
# box and *prepended* by the composer, so each round trip added another copy: a screen
# touched four times sent `feminine slight average build.` four times before it got to the
# shot. And an old format's prompt stayed pinned under a new format's name and price —
# including onto a voice-over, where a TTS provider was handed a camera direction to read
# aloud in the artist's voice.
def _box(page: str, index: int = 0) -> str:
    """What the prompt box on the page actually holds, unescaped.

    Read out of the markup rather than recomputed, because the whole claim under test is
    about the value that makes the round trip — what the server rendered is the only thing
    a browser can hand back.
    """
    match = re.search(rf'<textarea name="prompt_{index}"[^>]*>(.*?)</textarea>', page, re.S)
    assert match, f"no prompt box for shot {index} on the page"
    return html.unescape(match.group(1))


def _generate(client, song, artist, **params):
    return client.get(f"/console/artists/{artist.id}/songs/{song.id}/generate", params=params)


def test_the_screen_echoes_what_it_filled_the_prompt_box_with(client, song, two_faces):
    """The mechanism the two tests below depend on, asserted directly.

    Without the echo the server cannot tell its own prefill from a rewrite, because both
    arrive as `prompt_0` with a value in it.
    """
    page = _generate(client, song, two_faces, video_count=1).text

    assert 'name="sent_prompt_0"' in page
    assert 'name="position_key_0"' in page


def test_re_pricing_an_untouched_box_does_not_pin_or_duplicate_the_prompt(
    client, song, two_faces
):
    """The compounding bug. Three round trips must leave the string exactly as it was."""
    params = {"video_count": 1, "faces_chosen": 1, "identity_names": ""}
    box = _box(_generate(client, song, two_faces, **params).text)
    assert box.count("feminine slight") == 1, "the fixture's identity, prepended once"

    first = box
    for _ in range(3):
        box = _box(
            _generate(client, song, two_faces, prompt_0=box, sent_prompt_0=box, **params).text
        )
        assert box.count("feminine slight") == 1, "the identity is being prepended again"
    assert box == first, "an untouched box drifted across re-pricing"


def test_switching_format_leaves_an_untouched_prompt_free_to_follow_it(client, song, two_faces):
    """A pinned prompt is how a plan comes to disagree with its own heading: the card said
    Direct address, priced a direct address, and sent a performance clip."""
    params = {"video_count": 1, "faces_chosen": 1, "identity_names": ""}
    box = _box(_generate(client, song, two_faces, recipe_slug="performance", **params).text)
    assert "performance clip" in box.lower()

    switched = _box(
        _generate(
            client, song, two_faces,
            recipe_slug="direct-address", line="See you at the Bowl on the 15th",
            prompt_0=box, sent_prompt_0=box, **params,
        ).text
    )

    assert "See you at the Bowl on the 15th" in switched
    assert "performance clip" not in switched.lower()


def test_an_edited_prompt_is_sent_exactly_as_typed(client, container, principal, song, two_faces):
    """The other half: a real edit still wins, and is not composed a second time.

    The box was showing the identity when it was typed into, so the string coming back
    already opens with it. Prepending again is what produced the duplication.
    """
    mine = "Static wide shot of an empty Miami balcony at dusk, no camera movement"

    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1,
        identity_names=[""], overrides={0: {"prompt": mine}},
    )

    assert plan.shots[0].prompt == mine


def test_an_edit_does_not_follow_its_position_into_a_different_shot(
    container, principal, song, two_faces
):
    """Index 1 of a two-clip performance brief and index 1 of a direct address with a
    voice-over are not the same shot, and the second one is read aloud.

    An override that survived the switch had ElevenLabs speaking "Cinematic vertical
    performance clip … slow push in" in the artist's voice, which is audible only after
    delivery.
    """
    from remixkit.ports.generator import position_key

    stale = "Cinematic vertical performance clip, medium shot, slow push in"
    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1,
        recipe_slug="direct-address", line="Hello everyone",
        tts_text="See you at the Bowl on the 15th",
        overrides={1: {"prompt": stale, "position_key": position_key(Modality.VIDEO, "performance")}},
    )

    spoken = [step for step in plan.shots if step.modality is Modality.AUDIO]
    assert spoken, "the brief asked for a voice-over"
    assert stale not in spoken[0].prompt, "a camera direction would have been read aloud"
    assert "See you at the Bowl on the 15th" in spoken[0].prompt


def test_an_edit_still_applies_when_the_position_still_means_the_same_thing(
    container, principal, song, two_faces
):
    """The guard drops edits that no longer fit; it must not drop the ones that do."""
    from remixkit.ports.generator import position_key

    mine = "Static wide shot of an empty Miami balcony at dusk"
    plan, _ = container.kits.plan(
        principal, song_id=song.id, video_count=1, recipe_slug="performance",
        overrides={0: {"prompt": mine, "position_key": position_key(Modality.VIDEO, "performance")}},
    )

    assert plan.shots[0].prompt == mine


def test_queueing_an_untouched_plan_stores_no_prompt_overrides(
    client, container, principal, song, two_faces
):
    """The button posts the priced form itself, so it carries the prefilled boxes too — and
    a brief that records them has frozen the prompt for the worker as well as the screen."""
    plan, _ = container.kits.plan(principal, song_id=song.id, video_count=1, identity_names=[""])
    sent = plan.shots[0].prompt

    client.post(
        "/ui/kits",
        data={"song_id": song.id, "artist_id": two_faces.id, "video_count": "1",
              "faces_chosen": "1", "identity_names": "",
              "prompt_0": sent, "sent_prompt_0": sent,
              "position_key_0": plan.shots[0].position_key},
    )
    kit = container.kits.list(principal)[0]

    assert kit.brief["shot_overrides"] == []


# ---------------------------------------------------------- who is in the kit
def test_the_screen_says_when_no_face_is_ticked(client, song, two_faces):
    """The pagebar used to describe the artist's newest identity whatever the picker said,
    so a kit with every box cleared read "v1 · 5 frames · face conditioned" over a plan
    whose every shot said the face was not going."""
    page = _generate(client, song, two_faces, video_count=1, faces_chosen=1).text

    assert "none ticked" in page
    assert "render a stranger" in page, "the format selected is one about the artist"
    assert "face conditioned" not in page


def test_the_screen_names_the_face_the_plan_is_actually_for(client, song, two_faces):
    page = _generate(
        client, song, two_faces, video_count=1, faces_chosen=1, identity_names="Marco"
    ).text

    assert "Marco" in page
    assert "none ticked" not in page
