"""Does the rest of the screen keep up.

Every console mutation returns the one region its form targeted, and for a long time it
left the rest of the page describing state that no longer existed — the nav tree, the
counts in the header bar, the tab strip, the catalogue. Each fragment was individually
correct, which is why no per-fragment test caught it: the bug is a property of the
*screen*, in the same way reachability is a property of the graph rather than of any one
page (see test_navigation.py).

So it is tested as one, in two halves that fail for different reasons.

The first half is the contract between a mutation and the vocabulary: a route that
changes a collection says so, a route that refuses says nothing, and the broad events are
not fired for narrow edits. That is what `HX-Trigger` carries.

The second half is the half that rots silently. A page names the regions it keeps live by
id, and nothing in HTML enforces that those ids exist — rename a wrapper and the live
update simply stops happening, with no error anywhere, on a screen that still renders
perfectly. So every id a page declares is checked against the page that declared it.
"""

from __future__ import annotations

import re

import pytest

from remixkit.ui import events


# ------------------------------------------------------------------ fixtures
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


@pytest.fixture
def kit(client, song):
    return client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1}).json()


def fired(response) -> list[str]:
    """The events a response announced, as a list.

    A header rather than a body, so this is what a browser would act on — and a list
    rather than a string because two audiences can share one response (a settled analysis
    wakes the sections panel by name *and* every region reading the song).
    """
    return [name.strip() for name in response.headers.get("HX-Trigger", "").split(",") if name]


# ------------------------------------------------------------------ the declared regions
_PAGES = [
    "/console",
    "/console/catalogue",
    # The filtered form of the same screen, because the live element re-reads the URL it
    # is on and dropping the query would quietly reload an unfiltered catalogue.
    "/console/catalogue?artist_id={artist_id}",
    "/console/artists/{artist_id}",
    "/console/artists/{artist_id}/identity",
    "/console/songs/{song_id}",
]


def _paths(artist_id: str, song_id: str) -> list[str]:
    return [p.format(artist_id=artist_id, song_id=song_id) for p in _PAGES]


@pytest.mark.parametrize("page", _PAGES)
def test_every_live_region_a_page_names_exists_on_that_page(client, page, kit, song, consented):
    """The declaration and the markup cannot drift apart without this failing.

    `hx-select-oob` is a list of ids htmx pulls out of the response; one that matches
    nothing is not an error, it is silence. That is the failure this test exists for —
    a renamed wrapper leaves a screen that renders correctly and stops updating, and the
    only symptom is a stale number somebody eventually notices.
    """
    url = page.format(artist_id=consented["id"], song_id=song["id"])
    body = client.get(url).text

    declared = re.search(r'hx-select-oob="([^"]+)"', body)
    assert declared, f"{url} renders no live-refresh element"

    for region in declared.group(1).split(","):
        region = region.strip()
        assert region.startswith("#"), f"{url} declares {region!r}, which is not an id selector"
        assert f'id="{region[1:]}"' in body, f"{url} keeps {region} live but has no such element"


@pytest.mark.parametrize("page", _PAGES)
def test_the_live_element_re_reads_the_page_it_is_on(client, page, song, consented):
    """Including the query string: the catalogue's filter lives there, and a live update
    that dropped it would silently swap a filtered table for an unfiltered one."""
    url = page.format(artist_id=consented["id"], song_id=song["id"])
    body = client.get(url).text

    element = re.search(r'<div id="rk-live"[^>]*>', body)
    assert element, f"{url} renders no live-refresh element"
    assert f'hx-get="{url}"' in element.group(0), f"{url} re-reads something else"


def test_every_page_listens_for_the_whole_vocabulary(client, consented):
    """An event nothing listens for is an event that does not exist. The layout builds
    this list from `events.ALL`, so adding one there is what makes it live everywhere —
    this is the assertion that keeps that true."""
    body = client.get(f"/console/artists/{consented['id']}").text
    trigger = re.search(r'hx-trigger="([^"]+)"[^>]*hx-select="#rk-live"', body)
    assert trigger, "the live element carries no trigger list"
    for event in events.ALL:
        assert f"{event} from:body" in trigger.group(1), f"nothing listens for {event}"


def test_the_rail_is_live_on_every_page(client, song, consented):
    """It is the one region on all of them, and the one the layout contributes itself."""
    for url in _paths(consented["id"], song["id"]):
        assert "#sidebar" in client.get(url).text, f"{url} does not keep the rail live"


# ------------------------------------------------------------------ what each change says
def test_registering_an_artist_announces_the_roster(client):
    response = client.post("/ui/artists", data={"name": "Ada Lark"})
    assert events.ARTISTS in fired(response)


def test_recording_consent_announces_the_roster(client, artist):
    """Consent is drawn by the nav dot, the roster card, the artist header and the
    catalogue's blocked count — none of which the consent panel's own swap reaches."""
    response = client.post(
        f"/ui/artists/{artist['id']}/consent",
        data={"granted": "true", "signed_by": "manager@label.example"},
    )
    assert events.ARTISTS in fired(response)


def test_renaming_an_artist_announces_the_roster(client, artist):
    response = client.post(f"/ui/artists/{artist['id']}", data={"name": "Nocturnal II"})
    assert events.ARTISTS in fired(response)


def test_attaching_a_song_announces_the_songs(client, consented):
    response = client.post(
        f"/ui/artists/{consented['id']}/songs", data={"title": "Second Wind"}
    )
    assert events.SONGS in fired(response)


def test_renaming_a_song_announces_the_songs(client, song):
    """A title is drawn by the nav tree, the artist's list, the breadcrumb and every
    catalogue row — the broad event, not the narrow one."""
    response = client.post(f"/ui/songs/{song['id']}", data={"title": "Losing Sleep (edit)"})
    assert events.SONGS in fired(response)


def test_measuring_a_song_announces_the_song_and_not_the_roster(client, song):
    """The two song events are separate on purpose.

    Measuring changes the page you are standing on; it does not change what any other
    screen *lists*. Firing the broad event here would repaint the nav tree on every
    save of a number, which is how a live console becomes a slow one.
    """
    response = client.post(
        f"/ui/songs/{song['id']}/measurement", data={"bpm": "128", "bpm_method": "tapped"}
    )
    announced = fired(response)
    assert events.SONG in announced
    assert events.SONGS not in announced


def test_editing_the_words_announces_the_song(client, song):
    response = client.post(f"/ui/songs/{song['id']}/lyrics/text", data={"text": "one\ntwo"})
    assert events.SONG in fired(response)


def test_marking_a_section_announces_the_song(client, song):
    response = client.post(
        f"/ui/songs/{song['id']}/sections",
        data={"start_ms": "1000", "end_ms": "9000", "role": "hook", "label": "drop"},
    )
    assert events.SONG in fired(response)


def test_queueing_a_kit_announces_the_kits(client, song, consented):
    response = client.post(
        "/ui/kits", data={"song_id": song["id"], "artist_id": consented["id"], "video_count": "1"}
    )
    assert events.KITS in fired(response)


def test_approving_a_kit_announces_the_kits(client, container, principal, song):
    """The catalogue's "no approved kit" gap is the label's daily question, and it is
    counted a page away from the row that clears it.

    The kit is run first because approval is refused on one that has not finished —
    approval is the editorial axis and it only opens once the technical one has settled.
    """
    kit = container.kits.request(principal, song_id=song["id"], video_count=1)
    container.kits.run(principal.tenant_id, kit.id)

    response = client.post(f"/ui/kits/{kit.id}/approval", data={"state": "approved"})
    assert response.status_code == 200
    assert events.KITS in fired(response)


def test_saving_an_identity_announces_the_identity(client, artist):
    response = client.post(
        f"/ui/artists/{artist['id']}/identity", data={"structural_features": "sharp jaw"}
    )
    assert events.IDENTITY in fired(response)


def test_deleting_an_artist_announces_everything_it_took_with_it(client, song, consented):
    """A cascade removes the artist's songs, identities and kits. Announcing only the
    roster would leave the nav tree holding a branch of records that no longer resolve."""
    response = client.delete(f"/ui/artists/{consented['id']}?cascade=true")
    announced = fired(response)
    for event in (events.ARTISTS, events.SONGS, events.KITS, events.IDENTITY):
        assert event in announced, f"a cascade did not announce {event}"


# ------------------------------------------------------------------ what stays quiet
def test_a_refusal_announces_nothing(client, artist):
    """Nothing changed, so nothing should re-read. On the roster an announcement is
    three repository scans to redraw an identical screen."""
    client.post(f"/api/v1/artists/{artist['id']}/songs", json={"title": "Held"})

    response = client.delete(f"/ui/artists/{artist['id']}")
    assert response.status_code == 409, "this artist still has a song and cannot be removed"
    assert fired(response) == []


def test_a_bogus_approval_state_announces_nothing(client, kit):
    response = client.post(f"/ui/kits/{kit['id']}/approval", data={"state": "shipped"})
    assert response.status_code == 400
    assert fired(response) == []


def test_a_running_kit_does_not_announce_on_every_poll(client, container, principal, song):
    """The row polls itself every three seconds for the length of a run.

    Announcing on each one would re-render the whole page every three seconds — the
    interesting moment is the run *landing*, which is also the last poll, since the
    terminal markup carries no trigger.
    """
    from remixkit.domain.models import KitStatus

    kit = container.kits.request(principal, song_id=song["id"], video_count=1)
    record = container.kits.get(principal, kit.id)
    assert record.status in (KitStatus.QUEUED, KitStatus.RUNNING), "fixture is already settled"

    assert fired(client.get(f"/ui/kits/{kit.id}/row")) == []


def test_a_settled_kit_announces_once(client, container, principal, song):
    """What repaints the nav's running dot, the Kits counts and the catalogue — none of
    which the row's own swap can reach."""
    kit = container.kits.request(principal, song_id=song["id"], video_count=1)
    container.kits.run(principal.tenant_id, kit.id)

    assert events.KITS in fired(client.get(f"/ui/kits/{kit.id}/row"))


def test_reading_a_page_announces_nothing(client, song, consented):
    """The live element re-reads the page it is on. A page route that announced anything
    would make that a loop that never settles."""
    for url in _paths(consented["id"], song["id"]):
        assert fired(client.get(url)) == [], f"{url} announces on a plain read"
