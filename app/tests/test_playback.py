"""Hearing the song — the whole record, and every section of it on its own.

A section is a window into the master, not a file of its own. That is the whole design:
one `<audio>` on the page, and buttons everywhere that say which millisecond to start at
and which to stop at. These cover the two halves that makes true — the markup carrying a
window per section, and the file route answering the range requests a seek needs.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def unmastered(client, container, principal):
    """A song with sections but no master. Nothing to play, and it must say so."""
    artist = container.artists.create(principal, name="Nocturnal")
    song = container.songs.create(principal, artist.id, title="No Master Yet")
    return container.songs.add_section(principal, song.id, start_ms=31_000, end_ms=37_400)


# ------------------------------------------------------------------ the page
def test_the_song_page_plays_the_whole_song(client, measured_song):
    page = client.get(f"/console/songs/{measured_song.id}").text

    assert '<audio id="rk-master"' in page
    # The src is minted from the key at render time, not stored — on local storage that
    # is the `/files/…` route.
    assert f"/files/{measured_song.master_key}" in page
    assert 'data-play="whole"' in page


def test_every_section_carries_the_window_it_plays(client, measured_song):
    page = client.get(f"/console/songs/{measured_song.id}").text

    assert measured_song.sections, "the fixture should have measured sections"
    for section in measured_song.sections:
        assert f'data-play="{section.id}"' in page
        assert (
            f'data-start="{section.start_ms}" data-end="{section.end_ms}"' in page
        ), f"{section.display_name} must play its own window, not the song"


def test_the_sections_fragment_keeps_its_play_buttons(client, measured_song):
    """The list is swapped by htmx on every edit; playback has to survive the swap."""
    fragment = client.get(f"/ui/songs/{measured_song.id}/hooks").text

    assert "<html" not in fragment.lower(), "a fragment route must return a fragment"
    for section in measured_song.sections:
        assert f'data-play="{section.id}"' in fragment


def test_a_song_with_no_master_has_nothing_to_play(client, unmastered):
    page = client.get(f"/console/songs/{unmastered.id}").text

    assert "<audio" not in page
    assert 'data-play="' not in page, "a play button over nothing is a button that lies"
    assert "Upload a master" in page


# ------------------------------------------------------------------ the bytes
def test_the_master_is_served_whole_and_says_it_takes_ranges(client, measured_song):
    whole = client.get(f"/files/{measured_song.master_key}")

    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.content == b"RIFF-not-really-audio"


def test_a_range_is_what_lets_a_section_be_seeked_to(client, measured_song):
    """Without a 206 the browser downloads the whole master before it will move the
    playhead — which on a real WAV is the difference between auditioning a hook and
    waiting for one."""
    whole = client.get(f"/files/{measured_song.master_key}").content

    part = client.get(f"/files/{measured_song.master_key}", headers={"Range": "bytes=5-9"})

    assert part.status_code == 206
    assert part.content == whole[5:10]
    assert part.headers["content-range"] == f"bytes 5-9/{len(whole)}"


@pytest.mark.parametrize(
    "header,expected",
    [
        ("bytes=5-", slice(5, None)),        # from here to the end
        ("bytes=-6", slice(-6, None)),       # the last six bytes
        ("bytes=0-0", slice(0, 1)),          # the probe a player opens with
        ("bytes=0-9999", slice(0, None)),    # past the end is clamped, not refused
    ],
)
def test_the_shapes_a_media_element_actually_sends(client, measured_song, header, expected):
    whole = client.get(f"/files/{measured_song.master_key}").content

    response = client.get(f"/files/{measured_song.master_key}", headers={"Range": header})

    assert response.status_code == 206
    assert response.content == whole[expected]


def test_a_range_that_starts_past_the_end_is_refused(client, measured_song):
    response = client.get(f"/files/{measured_song.master_key}", headers={"Range": "bytes=900-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */21"


def test_a_header_this_does_not_parse_serves_the_whole_object(client, measured_song):
    """Refusing a fetch over a header we chose not to understand is worse than serving it."""
    response = client.get(
        f"/files/{measured_song.master_key}", headers={"Range": "bytes=0-1, 4-6"}
    )

    assert response.status_code == 200
    assert response.content == b"RIFF-not-really-audio"
