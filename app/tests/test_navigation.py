"""Can you get there from here.

Every other screen test asks whether a page renders the right thing when you ask for it
by URL. None of them asked whether anything in the console *links* to it — so the kit
page, which carries the assets, the brief, the per-asset cost ledger and the manifest,
sat unreachable: no template in the app contained the string `/console/kits/`. It
rendered perfectly for anyone who typed the id in by hand.

That is not a bug a per-page test can catch, because each page was individually correct.
It is a property of the graph, so it is tested as one: crawl outward from the roster and
assert every page turns up. A new screen that nothing links to now fails here rather
than being discovered by someone who cannot find it.
"""

from __future__ import annotations

import re

import pytest


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


@pytest.fixture
def ready(client, container, principal, song):
    """A song with nothing blocking it — measured, hooked, mastered, consent on file.

    The catalogue renders a blocked row and a ready row completely differently (one names
    what is missing, the other offers the generate screen), so the two branches need two
    fixtures. `song` is the blocked one; every gap it has is closed here.
    """
    client.patch(
        f"/api/v1/songs/{song['id']}/measurement",
        json={"bpm": 125.0, "bpm_method": "tapped"},
    )
    client.patch(f"/api/v1/songs/{song['id']}/hook", json={"start_ms": 124_784, "end_ms": 131_184})
    key = f"remixkit/masters/{principal.tenant_id}/{song['id']}.wav"
    container.storage.put(key, b"RIFF....fake", content_type="audio/wav")
    container.songs.register_master(principal, song["id"], key=key, content_type="audio/wav")
    return song


# Followed links that are not pages: the JSON API, the file server, the generated API
# docs, the health endpoint, the htmx fragment routes, and sign-out — which the crawler
# must not follow for the obvious reason that it would then be crawling as nobody.
_NOT_A_PAGE = ("/api/", "/files/", "/docs", "/redoc", "/openapi", "/healthz", "/ui/", "/static/",
               "/auth/logout")


def _crawl(client, start: str = "/console", limit: int = 60) -> set[str]:
    """Every path reachable from `start` by following `href`s, breadth first.

    Returns paths with the query string dropped, because reachability is a question
    about screens: `/console/catalogue?artist_id=x` and `/console/catalogue` are the same
    screen, and a crawler that treated them as different would report coverage it does
    not have.
    """
    seen: set[str] = set()
    queue = [start]
    while queue and len(seen) < limit:
        url = queue.pop(0)
        path = url.split("?", 1)[0].split("#", 1)[0]
        if path in seen or not path.startswith("/") or path.startswith(_NOT_A_PAGE):
            continue
        response = client.get(url)
        # A link that 404s is a broken link, and this is the only test positioned to
        # notice: every path here came out of a template rather than out of a fixture.
        assert response.status_code == 200, f"{url} is linked to from the console but returns {response.status_code}"
        seen.add(path)
        queue.extend(re.findall(r'href="([^"]+)"', response.text))
    return seen


# ------------------------------------------------------------------ reachability
def test_every_console_page_is_reachable_from_the_roster(client, kit, song, consented):
    """The whole console, by clicking, starting from the page a sign-in lands on."""
    reached = _crawl(client)
    artist_id, song_id = consented["id"], song["id"]
    for page in (
        "/console",
        "/console/catalogue",
        "/console/settings",
        "/verify",
        f"/console/artists/{artist_id}",
        f"/console/artists/{artist_id}/identity",
        f"/console/songs/{song_id}",
        # The page this test was written for.
        f"/console/kits/{kit['id']}",
        # And the one the report named: it had exactly one way in, from the song page.
        f"/console/artists/{artist_id}/songs/{song_id}/generate",
    ):
        assert page in reached, f"nothing in the console links to {page}"


def test_the_generate_screen_has_more_than_one_way_in(client, kit, song, consented):
    """Cost-before-the-button is only worth building if people arrive at it.

    It is the priced route to a kit, and the artist page's quick form is the unpriced
    one, so this screen has to be reachable from wherever somebody is when they decide
    to spend — the song, the artist's song list, the catalogue row, and the kit they
    just looked at.
    """
    generate = f"/console/artists/{consented['id']}/songs/{song['id']}/generate"
    sources = (
        f"/console/songs/{song['id']}",
        # The one that was missing: the artist page's only generate control was the quick
        # form at the foot of the Kits tab, which queues without ever showing a price.
        f"/console/artists/{consented['id']}",
        f"/console/kits/{kit['id']}",
    )
    for page in sources:
        assert f'href="{generate}"' in client.get(page).text, f"{page} does not reach the generate screen"


def test_a_ready_catalogue_row_offers_the_generate_screen(client, ready, consented):
    """The fourth way in, and the only one that starts from "what should I work on"."""
    generate = f"/console/artists/{consented['id']}/songs/{ready['id']}/generate"
    assert f'href="{generate}"' in client.get("/console/catalogue").text


def test_a_blocked_catalogue_row_links_to_what_would_unblock_it(client, song):
    """The Blocked on column is a to-do list, so each item is the way to do it.

    The column named the reasons as plain text, leaving the reader to work out which
    screen each one is fixed on. These three are the song's own.
    """
    row = client.get("/console/catalogue").text
    for reason in ("measurement", "hook window", "master"):
        assert f'href="/console/songs/{song["id"]}">{reason}</a>' in row


def test_a_consent_gap_links_to_the_artist_and_not_the_song(client):
    """The one blocker of the four that lives on a different record.

    Consent is recorded on the artist, so a row blocked on it has to point there — a link
    to the song would land the reader on a page with no consent control on it.
    """
    artist = client.post("/api/v1/artists", json={"name": "Unsigned"}).json()
    client.post(f"/api/v1/artists/{artist['id']}/songs", json={"title": "No Rights"})

    row = client.get("/console/catalogue").text
    assert f'href="/console/artists/{artist["id"]}">likeness consent</a>' in row


def test_a_kit_names_itself_as_a_link_wherever_it_is_listed(client, kit, song, consented):
    """The three surfaces that list kits: the roster rail, the artist page, the song page."""
    for page in (
        "/console",
        f"/console/artists/{consented['id']}",
        f"/console/songs/{song['id']}",
    ):
        assert f'/console/kits/{kit["id"]}' in client.get(page).text, f"{page} lists a kit it cannot open"


def test_a_song_is_openable_from_the_artist_that_owns_it(client, song, consented):
    """The row linked to the song only when it had two or more hooks marked, so the
    ordinary case — one hook — was a dead end on the page that lists songs."""
    page = client.get(f"/console/artists/{consented['id']}")
    assert f'href="/console/songs/{song["id"]}"' in page.text


# ------------------------------------------------------------------ accessibility
_PAGES = [
    "/console",
    "/console/catalogue",
    "/console/settings",
    "/verify",
    "/console/artists/{artist_id}",
    "/console/artists/{artist_id}/identity",
    "/console/songs/{song_id}",
    "/console/artists/{artist_id}/songs/{song_id}/generate",
]


def _paths(artist_id: str, song_id: str) -> list[str]:
    return [p.format(artist_id=artist_id, song_id=song_id) for p in _PAGES]


def test_every_console_page_offers_a_skip_link(client, song, consented):
    """Ahead of the topbar, the banner, the breadcrumb, the identity bar and the rail."""
    for path in _paths(consented["id"], song["id"]):
        text = client.get(path).text
        assert 'href="#main"' in text, f"{path} has no skip link"
        assert 'id="main"' in text, f"{path} has no skip target"


def test_the_topbar_says_which_section_you_are_in(client, song, consented):
    """`aria-current` rather than a class, so the highlight is also announced.

    Exactly one link carries it — a nav that marks two sections current is telling a
    screen reader something false — and the deep pages report Roster, which is the
    section an artist, a song, a kit and the generate screen all hang off.
    """
    expected = {
        "/console/catalogue": "/console/catalogue",
        "/console/settings": "/console/settings",
        "/verify": "/verify",
    }
    for path in _paths(consented["id"], song["id"]):
        text = client.get(path).text
        current = re.findall(r'<a href="([^"]+)" aria-current="page"', text)
        assert len(current) == 1, f"{path} marks {current} as current"
        assert current[0] == expected.get(path, "/console"), f"{path} marks {current[0]}"


def test_a_breadcrumb_is_a_landmark_and_its_separators_are_not_read_aloud(client, song, consented):
    """It was a `<p>` of links with literal " / " between them — navigable by eye, and
    announced as "Roster slash Nocturnal slash Losing Sleep" on every page load."""
    deep = [
        f"/console/artists/{consented['id']}",
        f"/console/songs/{song['id']}",
        f"/console/artists/{consented['id']}/identity",
        f"/console/artists/{consented['id']}/songs/{song['id']}/generate",
    ]
    for path in deep:
        text = client.get(path).text
        assert 'aria-label="Breadcrumb"' in text, f"{path} has no breadcrumb landmark"
        trail = text.split('aria-label="Breadcrumb"', 1)[1].split("</nav>", 1)[0]
        assert "<ol>" in trail, f"{path}'s breadcrumb is not a list"
        # The current page is named and is not a link back to itself.
        assert 'aria-current="page"' in trail, f"{path}'s breadcrumb does not say where you are"
        assert "/" not in re.sub(r"<[^>]+>", "", trail), f"{path} draws its separators as text"


def test_the_kit_breadcrumb_survives_a_deleted_song(client, container, principal, kit):
    """A kit outlives its song — `routes.kit_detail` reads both through `_optional` for
    exactly that reason — so the trail is built up from whichever records still exist
    rather than written out. A crumb naming a deleted song would link to a 404."""
    container.repo.delete(principal.tenant_id, "songs", kit["song_id"])

    page = client.get(f"/console/kits/{kit['id']}")
    assert page.status_code == 200
    trail = page.text.split('aria-label="Breadcrumb"', 1)[1].split("</nav>", 1)[0]
    assert kit["song_id"] not in trail, "the trail links to a song that is gone"
    assert kit["name"] in trail, "the trail should still end on the kit itself"
