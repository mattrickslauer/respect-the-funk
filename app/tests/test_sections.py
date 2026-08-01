"""Many hooks per song, and the provenance that comes with each one.

A single `hook` window claimed every song has exactly one loopable moment, which is false
of every song with a chorus that comes back. These cover the list that replaced it — and
in particular the rule that keeps it honest: a window a person moved stops being the
analyser's claim, and says so.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import Provenance, SectionRole
from remixkit.services.briefs import default_shot_plan, hook_windows


@pytest.fixture
def artist(client):
    return client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()


@pytest.fixture
def consented(client, artist):
    client.put(
        f"/api/v1/artists/{artist['id']}/consent",
        json={"granted": True, "signed_by": "manager@label.example"},
    )
    return artist


@pytest.fixture
def song(client, consented):
    return client.post(
        f"/api/v1/artists/{consented['id']}/songs", json={"title": "Losing Sleep"}
    ).json()


def add(client, song, **body):
    body.setdefault("role", "chorus")
    return client.post(f"/api/v1/songs/{song['id']}/sections", json=body)


# ------------------------------------------------------------------ the list
def test_a_song_carries_as_many_hooks_as_it_has(client, song):
    add(client, song, start_ms=31_000, end_ms=37_400, label="Chorus 1")
    add(client, song, start_ms=94_000, end_ms=100_400, label="Chorus 2")
    add(client, song, start_ms=124_784, end_ms=131_184, label="Drop", role="drop")

    listed = client.get(f"/api/v1/songs/{song['id']}/sections").json()
    assert [s["label"] for s in listed["sections"]] == ["Chorus 1", "Chorus 2", "Drop"]
    # In song order, not insertion order — the list is read as a timeline.
    assert [s["start_ms"] for s in listed["sections"]] == [31_000, 94_000, 124_784]


def test_a_verse_is_markable_but_not_loopable(client, container, principal, song):
    add(client, song, start_ms=10_000, end_ms=40_000, role="verse")
    stored = container.songs.get(principal, song["id"])

    assert len(stored.sections) == 1
    assert stored.hook_sections == [], "a verse is a poor thing to loop"
    assert stored.primary_section_id is None


def test_the_first_hook_marked_becomes_the_hook_of_record(client, container, principal, song):
    add(client, song, start_ms=31_000, end_ms=37_400)
    add(client, song, start_ms=94_000, end_ms=100_400)
    stored = container.songs.get(principal, song["id"])

    assert stored.primary_section.start_ms == 31_000
    # The window on the song mirrors it, so a kit and the sections list cannot disagree.
    assert (stored.hook.start_ms, stored.hook.end_ms) == (31_000, 37_400)


def test_making_another_section_primary_moves_the_window(client, container, principal, song):
    add(client, song, start_ms=31_000, end_ms=37_400)
    second = add(client, song, start_ms=94_000, end_ms=100_400).json()
    section_id = second["sections"][-1]["id"]

    client.put(f"/api/v1/songs/{song['id']}/sections/{section_id}/primary")
    stored = container.songs.get(principal, song["id"])
    assert (stored.hook.start_ms, stored.hook.end_ms) == (94_000, 100_400)


def test_setting_the_hook_window_writes_through_to_the_sections_list(
    client, container, principal, song
):
    """The oldest surface in the console still works, and no longer lies about the list."""
    client.patch(f"/api/v1/songs/{song['id']}/hook", json={"start_ms": 1_000, "end_ms": 7_000})
    stored = container.songs.get(principal, song["id"])

    assert len(stored.sections) == 1
    assert stored.primary_section.role is SectionRole.HOOK
    assert stored.primary_section.duration_ms == 6_000


def test_an_end_before_a_start_is_a_refusal_not_a_traceback(client, song):
    response = add(client, song, start_ms=40_000, end_ms=39_000)
    assert response.status_code == 409
    assert "end after it starts" in response.text


def test_an_unknown_role_names_the_ones_that_exist(client, container, principal, song):
    from remixkit.services.errors import Conflict

    with pytest.raises(Conflict) as exc:
        container.songs.add_section(
            principal, song["id"], start_ms=0, end_ms=1_000, role="bangerpart"
        )
    assert "chorus" in str(exc.value)


def test_removing_the_primary_section_keeps_the_window(client, container, principal, song):
    """Tidying a list must not change what the next kit renders."""
    created = add(client, song, start_ms=31_000, end_ms=37_400).json()
    section_id = created["sections"][0]["id"]

    client.delete(f"/api/v1/songs/{song['id']}/sections/{section_id}")
    stored = container.songs.get(principal, song["id"])

    assert stored.sections == []
    assert stored.primary_section_id is None
    assert stored.hook.duration_ms == 6_400, "the window every existing kit was cut to"


# ------------------------------------------------------------------ provenance
def test_moving_a_measured_window_makes_it_manual(container, principal, song, measured_song):
    """The rule that keeps the list honest.

    A window a person dragged still reading `measured` would be the provenance discipline
    defeated by the edit form, so the source flips and the old method survives as history.
    """
    stored = measured_song
    section = stored.hook_sections[0]
    assert section.source is Provenance.MEASURED

    moved = container.songs.update_section(
        principal, stored.id, section.id, start_ms=section.start_ms + 480
    )
    edited = moved.section(section.id)
    assert edited.source is Provenance.MANUAL
    assert edited.method.startswith("hand-edited (was:")
    assert edited.energy_low_band is None, "the features described the old window"


def test_renaming_a_measured_section_leaves_it_measured(container, principal, measured_song):
    section = measured_song.hook_sections[0]
    renamed = container.songs.update_section(
        principal,
        measured_song.id,
        section.id,
        label="The one everyone knows",
        start_ms=section.start_ms,
        end_ms=section.end_ms,
    )
    assert renamed.section(section.id).source is Provenance.MEASURED


# ------------------------------------------------------------------ rendering
def test_a_kit_deals_its_videos_across_the_chosen_hooks(container, principal, song):
    """Three hooks, three videos — one loop each, at each hook's own length."""
    stored = container.songs.add_section(principal, song["id"], start_ms=0, end_ms=4_000)
    stored = container.songs.add_section(principal, stored.id, start_ms=30_000, end_ms=38_000)
    stored = container.songs.add_section(principal, stored.id, start_ms=60_000, end_ms=70_000)
    ids = [s.id for s in stored.hook_sections]

    shots = default_shot_plan(stored, None, video_count=3, section_ids=ids)
    assert [s.seconds for s in shots] == [4.0, 8.0, 10.0]
    # Every loop says which hook it belongs to, which is the only way to read the plan.
    assert all(shot.label for shot in shots)


def test_more_videos_than_hooks_cycles_the_moods(container, principal, song):
    stored = container.songs.add_section(principal, song["id"], start_ms=0, end_ms=4_000)
    stored = container.songs.add_section(principal, stored.id, start_ms=30_000, end_ms=38_000)
    ids = [s.id for s in stored.hook_sections]

    shots = default_shot_plan(stored, None, video_count=4, section_ids=ids)
    assert [s.seconds for s in shots] == [4.0, 8.0, 4.0, 8.0]
    assert len({s.prompt for s in shots}) == 4, "four distinct moods, not two repeated"


def test_a_deleted_section_is_dropped_not_substituted(container, principal, song):
    """A kit that renders a different part of the song than its brief says is worse than
    one that renders a loop fewer."""
    stored = container.songs.add_section(principal, song["id"], start_ms=0, end_ms=4_000)
    assert hook_windows(stored, ["sec_gone"]) == [("hook", stored.hook)]


def test_a_kit_records_the_windows_it_was_bought_at(container, principal, song):
    stored = container.songs.add_section(principal, song["id"], start_ms=30_000, end_ms=38_000)
    section_id = stored.hook_sections[0].id

    kit = container.kits.request(
        principal, song_id=stored.id, video_count=1, section_ids=[section_id]
    )
    assert kit.brief["section_ids"] == [section_id]
    assert kit.brief["hook_windows"] == [
        {"name": stored.hook_sections[0].display_name, "start_ms": 30_000, "end_ms": 38_000}
    ]


# ------------------------------------------------------------------ the console
def test_the_song_page_lists_sections_and_the_hook_of_record(client, container, principal, song):
    container.songs.add_section(
        principal, song["id"], start_ms=31_000, end_ms=37_400, label="Chorus 1"
    )
    page = client.get(f"/console/songs/{song['id']}")

    assert page.status_code == 200
    assert "Chorus 1" in page.text and "hook of record" in page.text


def test_the_section_fragment_routes_return_fragments(client, song):
    added = client.post(
        f"/ui/songs/{song['id']}/sections",
        data={"start_ms": "31000", "end_ms": "37400", "role": "chorus", "label": "Chorus 1"},
    )
    assert added.status_code == 200
    assert "<html" not in added.text.lower(), "a fragment route must return a fragment"
    assert "Chorus 1" in added.text


def test_marking_a_backwards_window_through_the_ui_is_a_refusal(client, song):
    response = client.post(
        f"/ui/songs/{song['id']}/sections", data={"start_ms": "9000", "end_ms": "1000"}
    )
    assert response.status_code == 409


def test_the_generate_form_buys_exactly_the_hooks_it_priced(client, container, principal, song):
    """Repeated `section_ids` fields, which is how the checkbox list arrives."""
    stored = container.songs.add_section(principal, song["id"], start_ms=0, end_ms=4_000)
    stored = container.songs.add_section(principal, stored.id, start_ms=30_000, end_ms=38_000)
    ids = [s.id for s in stored.hook_sections]

    priced = client.get(
        f"/console/artists/{song['artist_id']}/songs/{song['id']}/generate",
        params={"video_count": 2, "section_ids": ids},
    )
    assert priced.status_code == 200
    assert "4.0s" in priced.text and "8.0s" in priced.text

    queued = client.post(
        "/ui/kits",
        data={
            "song_id": song["id"],
            "artist_id": song["artist_id"],
            "video_count": "2",
            "section_ids": ids,
        },
    )
    assert queued.status_code == 200
    kit = container.kits.list(principal)[0]
    assert kit.brief["section_ids"] == ids


def test_the_catalogue_counts_a_marked_section_as_a_hook(client, container, principal, song):
    assert container.catalogue.gaps(principal).rows[0].has_hook is False
    container.songs.add_section(principal, song["id"], start_ms=31_000, end_ms=37_400)
    assert container.catalogue.gaps(principal).rows[0].has_hook is True
