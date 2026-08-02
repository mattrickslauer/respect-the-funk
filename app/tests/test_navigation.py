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


def _sidenav(text: str) -> str:
    return text.split('class="sidenav"', 1)[1].split("</nav>", 1)[0]


def test_the_sidebar_says_where_you_are(client, song, consented):
    """`aria-current` rather than a class, so the highlight is also announced.

    Exactly one item in the rail carries it — a nav that marks two places current is
    telling a screen reader something false. Scoped to the rail because breadcrumbs and
    the identity line switcher legitimately carry the same attribute elsewhere.
    """
    artist_id, song_id = consented["id"], song["id"]
    expected = {
        "/console": "/console",
        "/console/catalogue": "/console/catalogue",
        "/console/settings": "/console/settings",
        "/verify": "/verify",
        f"/console/artists/{artist_id}": f"/console/artists/{artist_id}",
        f"/console/artists/{artist_id}/identity": f"/console/artists/{artist_id}/identity",
        f"/console/songs/{song_id}": f"/console/songs/{song_id}",
    }
    for path, href in expected.items():
        rail = _sidenav(client.get(path).text)
        current = re.findall(r'href="([^"]+)"[^>]*aria-current="page"', rail)
        assert len(current) == 1, f"{path} marks {current} as current in the rail"
        assert current[0] == href, f"{path} marks {current[0]}, expected {href}"


def test_the_rail_expands_the_artist_you_are_inside_and_no_other(client, song, consented):
    """The tree is contextual, and only one branch opens.

    Expanding every artist would put one repository scan per roster entry behind every
    page render, and a tree nobody can find anything in — `ui.routes._nav` says so, and
    this is what holds it to it.
    """
    other = client.post("/api/v1/artists", json={"name": "Somebody Else"}).json()
    client.post(f"/api/v1/artists/{other['id']}/songs", json={"title": "Not Listed"})

    rail = _sidenav(client.get(f"/console/artists/{consented['id']}").text)
    assert song["title"] in rail, "the artist you are inside does not list its songs"
    assert "Somebody Else" in rail, "every artist should still be a link"
    assert "Not Listed" not in rail, "a collapsed artist listed its songs"


def test_a_song_in_the_rail_stays_current_while_pricing_a_kit(client, song, consented):
    """The generate screen hangs off a song and does not move you off it."""
    rail = _sidenav(
        client.get(
            f"/console/artists/{consented['id']}/songs/{song['id']}/generate"
        ).text
    )
    assert f'href="/console/songs/{song["id"]}"' in rail
    assert "is-on" in rail


# ------------------------------------------------------------------ layers
# What htmx sends when a `layer()` link fires: the id of the element it will swap.
# `HX-Target` rather than `HX-Request` is what `_screen` keys on — see its docstring.
_HTMX = {"HX-Request": "true", "HX-Target": "layer"}

_SECONDARY = ["/verify", "/console/kits/{kit_id}",
              "/console/artists/{artist_id}/songs/{song_id}/generate"]


def _secondary(artist_id: str, song_id: str, kit_id: str) -> list[str]:
    return [p.format(artist_id=artist_id, song_id=song_id, kit_id=kit_id) for p in _SECONDARY]


def test_a_secondary_screen_is_a_page_or_a_layer_depending_who_asked(client, kit, song, consented):
    """One route, two frames — `ui.routes._screen`.

    Asked for plainly it is a whole page with the rail beside it; asked for by htmx it is
    a `<dialog>` with no shell around it, so it can be dropped into the layer over
    whatever the person was already looking at.
    """
    for path in _secondary(consented["id"], song["id"], kit["id"]):
        page = client.get(path)
        assert page.status_code == 200
        assert "<!doctype html>" in page.text.lower(), f"{path} is not a page"
        assert 'class="sidenav"' in page.text, f"{path} lost the rail"

        layer = client.get(path, headers=_HTMX)
        assert layer.status_code == 200, f"{path} failed as a layer"
        assert "<dialog" in layer.text, f"{path} did not come back as a dialog"
        assert "<!doctype html>" not in layer.text.lower(), f"{path} sent a whole page into the layer"
        assert 'class="sidenav"' not in layer.text, f"{path} put a second rail inside the layer"


def test_both_frames_render_the_same_body(client, kit, song, consented):
    """The point of sharing `components/_<name>_body.html`: two definitions of what a kit
    costs would eventually disagree about it, and the estimate is this console's whole
    argument for the generate screen existing."""
    generate = f"/console/artists/{consented['id']}/songs/{song['id']}/generate"
    page = client.get(generate).text
    layer = client.get(generate, headers=_HTMX).text

    estimate = re.search(r"<strong data-estimate>([^<]+)</strong>", page)
    assert estimate, "the page lost its machine-readable estimate"
    assert f"<strong data-estimate>{estimate.group(1)}</strong>" in layer, (
        "the layer prices the same plan differently from the page"
    )


def test_every_layer_link_is_also_an_ordinary_link(client, kit, song, consented):
    """`components/_macros.html` emits `href` beside `hx-get` so the console degrades to
    plain navigation with no JavaScript at all. An `hx-get` that lost its `href` still
    works in a browser and silently stops working everywhere else."""
    for page in ("/console", f"/console/artists/{consented['id']}", f"/console/songs/{song['id']}",
                 "/console/catalogue"):
        text = client.get(page).text
        for tag in re.findall(r"<a\b[^>]*hx-get=[^>]*>", text):
            assert "href=" in tag, f"{page} has a layer link with no href: {tag[:120]}"


def test_a_layer_can_be_closed_and_expanded(client, kit, song, consented):
    """Escape and the backdrop are the browser's; these two are ours."""
    for path in _secondary(consented["id"], song["id"], kit["id"]):
        layer = client.get(path, headers=_HTMX).text
        assert "data-close-layer" in layer, f"{path} opens with no way to close"
        assert f'href="{path}"' in layer, f"{path} cannot be expanded to its own page"


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


# ------------------------------------------------------------------ the rail's reach
def test_collapsing_the_rail_only_hides_things_in_the_rail():
    """A static check, in the spirit of `test_nothing_reaches_for_a_bare_z_index`.

    The collapsed rail hides everything in it that is a word — `.navlabel`, `.count`,
    `.brand-name`. None of those are the rail's private names: `.count` is the badge on a
    tab strip too, so unscoped, collapsing the rail also stripped the counts off the song
    page's tabs. A page must not tell you less because a *different* region got narrower,
    and the failure is invisible until somebody collapses the rail and looks elsewhere.
    """
    from pathlib import Path

    css = Path("remixkit/ui/static/console.css").read_text()
    leaked = []
    for block in css.split("}"):
        if 'data-nav="collapsed"' not in block or "display:none" not in block:
            continue
        for selector in block.split("{")[0].split(","):
            selector = selector.strip()
            if selector and "data-nav" in selector and ".sidebar" not in selector:
                leaked.append(selector)
    assert not leaked, f"collapsed-rail rules that reach outside the rail: {leaked}"


def test_repricing_the_plan_does_not_come_back_as_a_dialog(client, song, consented):
    """The generate screen's second htmx caller.

    The form re-prices itself against this same handler, targeting `#plan-body`. Keyed on
    `HX-Request` the answer would be an overlay — which happens to work, because
    `hx-select` pulls the same regions out of either frame, and that accident is exactly
    what this pins down before somebody relies on it.
    """
    generate = f"/console/artists/{consented['id']}/songs/{song['id']}/generate"
    repriced = client.get(
        generate, headers={"HX-Request": "true", "HX-Target": "plan-body"}
    )
    assert repriced.status_code == 200
    assert "<dialog" not in repriced.text, "a re-price was answered with a layer"
    assert 'id="plan-body"' in repriced.text, "the re-price response has nothing to select"
    assert 'id="plan-stats"' in repriced.text, "the out-of-band estimate is missing"
