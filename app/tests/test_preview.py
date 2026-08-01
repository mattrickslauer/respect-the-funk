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

    A kit loop is a backdrop the master plays over, so audio in it is a defect. This is a
    conditioning signal rather than a mute switch — see `briefs.VIDEO_NEGATIVES` — and the
    assertion is only that the signal is actually sent, which it previously was not.
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
    different kit than the one priced — the divergence this screen exists to prevent."""
    page = client.get(
        f"/console/artists/{two_faces.id}/songs/{song.id}/generate"
        "?video_count=1&faces_chosen=1&identity_names=Marco&tts_text=hello&budget_cents=900"
    ).text
    queue_form = page.split('hx-post="/ui/kits"', 1)[1].split("</form>", 1)[0]

    assert 'name="faces_chosen"' in queue_form
    assert 'value="Marco"' in queue_form
    assert 'name="tts_text"' in queue_form
    assert 'name="budget_cents"' in queue_form
