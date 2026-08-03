"""What a console page costs in billable reads.

B2 bills transactions, not bytes: 2,500 Class B a day, then it refuses — which is how a
78MB bucket took the whole console down with half its bandwidth unspent. So the number of
documents a page touches is a budget, and these are the guards on it.
"""

from __future__ import annotations

from collections import Counter

import pytest


@pytest.fixture
def reads(container, monkeypatch):
    """Count every object read, and how often the same key is read twice."""
    seen: Counter = Counter()
    real_get = container.storage.get

    def get(key):
        seen[key] += 1
        return real_get(key)

    monkeypatch.setattr(container.storage, "get", get)
    return seen


@pytest.fixture
def listings(container, monkeypatch):
    """Count every prefix scan, and how often the same prefix is scanned twice.

    The blind spot in the original budget work: it wrapped `get` and never `list`, so the
    scans that *find* the documents went unmeasured while the documents themselves were
    being counted to the transaction.
    """
    seen: Counter = Counter()
    real_list = container.storage.list

    def listing(prefix):
        seen[prefix] += 1
        return real_list(prefix)

    monkeypatch.setattr(container.storage, "list", listing)
    return seen


@pytest.fixture
def catalogue(container, principal):
    artist = container.artists.create(principal, name="Amanda Kurt")
    song = container.songs.create(principal, artist.id, title="Show Invite")
    for n in range(4):
        container.songs.create(principal, artist.id, title=f"Filler {n}")
    return artist, song


def test_no_page_reads_the_same_document_twice(container, principal, client, reads, catalogue):
    """The measurement that started this.

        page                      Class B   distinct   repeated
        /console                        2          1          1
        /console/catalogue              8          6          2
        /console/songs/{id}             8          6          2
        /console/artists/{id}          12          6          6

    One artist and five songs, so those were floors, and the repeats were proportional —
    the artist page read *every* document exactly twice. Nobody wrote it that way: the nav
    resolves the roster, the body resolves it again, a panel resolves it a third time, and
    each is correct on its own.

    A repeat is not only waste, it is a page that can contradict itself — a nav naming a
    song the body has already seen renamed.
    """
    artist, song = catalogue

    for url in (
        "/console",
        "/console/catalogue",
        f"/console/songs/{song.id}",
        f"/console/artists/{artist.id}",
    ):
        reads.clear()
        assert client.get(url, follow_redirects=False).status_code == 200
        repeated = {k: n for k, n in reads.items() if n > 1}
        assert not repeated, f"{url} paid twice for {list(repeated)}"


def test_the_artist_page_costs_what_it_touches(container, principal, client, reads, catalogue):
    """A floor on the saving, not just the absence of repeats: six documents exist and the
    page may not read more than six times."""
    artist, _song = catalogue

    client.get(f"/console/artists/{artist.id}", follow_redirects=False)

    assert sum(reads.values()) <= 6, dict(reads)


def test_no_page_lists_the_same_prefix_twice(container, principal, client, listings, catalogue):
    """The same measurement as above, for the scan rather than the object.

    Deduping reads left the listings untouched, and they repeated in exactly the same
    places and for exactly the same reason — the nav resolves the roster, the body
    resolves it again. Measured on one artist and five songs before this guard:

        page                      listings   distinct   repeated
        /console                         3          2          1
        /console/catalogue               5          3          2
        /console/songs/{id}              4          3          1
        /console/artists/{id}            7          4          3
    """
    artist, song = catalogue

    for url in (
        "/console",
        "/console/catalogue",
        f"/console/songs/{song.id}",
        f"/console/artists/{artist.id}",
    ):
        listings.clear()
        assert client.get(url, follow_redirects=False).status_code == 200
        repeated = {k: n for k, n in listings.items() if n > 1}
        assert not repeated, f"{url} scanned twice for {list(repeated)}"


def test_a_created_document_is_in_its_own_requests_listing(container, principal):
    """The snapshot must not outlive the truth here either. A handler that creates a song
    and then renders the roster — which is most of them — must see the row it just added,
    so a write drops the listing that would have been taken before it."""
    from remixkit.domain.models import Artist

    with container.repo.request_scope():
        before = container.repo.list(principal.tenant_id, "artists", Artist)
        container.artists.create(principal, name="Added Mid-Request")
        after = container.repo.list(principal.tenant_id, "artists", Artist)

    assert len(after) == len(before) + 1, "a cached listing hid a document this request wrote"
    assert any(a.name == "Added Mid-Request" for a in after)


def test_a_deleted_document_leaves_its_own_requests_listing(container, principal, catalogue):
    """The other half of the same rule."""
    from remixkit.domain.models import Song

    _artist, song = catalogue

    with container.repo.request_scope():
        before = container.repo.list(principal.tenant_id, "songs", Song)
        container.repo.delete(principal.tenant_id, "songs", song.id)
        after = container.repo.list(principal.tenant_id, "songs", Song)

    assert len(after) == len(before) - 1, "a cached listing kept a document this request deleted"


def test_nothing_is_listed_across_requests(container, principal, client, listings, catalogue):
    """Same boundary as the reads: the scope dies with the response, so the next page load
    pays for — and therefore sees — whatever is in the bucket now.

    Warmed first, because the very first console request of a tenant's life seeds the
    built-in recipes, and those writes correctly drop the recipe listing they invalidate —
    so an unwarmed first request scans once more than every request after it, for a reason
    that has nothing to do with the boundary being measured here.
    """
    artist, _song = catalogue
    client.get(f"/console/artists/{artist.id}", follow_redirects=False)
    listings.clear()

    client.get(f"/console/artists/{artist.id}", follow_redirects=False)
    first = sum(listings.values())
    listings.clear()
    client.get(f"/console/artists/{artist.id}", follow_redirects=False)

    assert sum(listings.values()) == first, "a second request must scan again, not reuse"


def test_a_listing_refusal_is_never_remembered(container, principal, monkeypatch):
    """A capped bucket refuses scans too, and remembering the refusal would outlive it."""

    class Refusal(Exception):
        error_code = type("Code", (), {"name": "ACCESS_DENIED"})()

    calls = [0]

    def refuse(_prefix):
        calls[0] += 1
        raise Refusal()

    monkeypatch.setattr(container.storage, "list", refuse)

    with container.repo.request_scope():
        for _ in range(3):
            with pytest.raises(Refusal):
                container.repo._listing("remixkit/tenants/t/songs")

    assert calls[0] == 3, "a refusal must be retried, not cached"


def test_listings_go_straight_through_outside_a_request(container, principal, listings, catalogue):
    """The worker and the CLI have no request boundary."""
    listings.clear()

    container.artists.list(principal)
    container.artists.list(principal)

    assert sum(listings.values()) == 2, "no scope means no caching"


def test_a_write_is_visible_to_the_rest_of_its_own_request(container, principal, client, catalogue):
    """The snapshot must not outlive the truth. Most handlers save and then render what
    they just saved, so a cached read from before the write would show the old title back
    to the person who just changed it."""
    _artist, song = catalogue

    response = client.post(
        f"/ui/songs/{song.id}", data={"title": "Renamed Mid-Request"}, follow_redirects=False
    )

    assert response.status_code < 400
    assert "Renamed Mid-Request" in response.text


def test_nothing_is_cached_across_requests(container, principal, client, reads, catalogue):
    """No invalidation to get wrong: the scope dies with the response, so the next page
    load pays for — and therefore sees — whatever is in the bucket now."""
    artist, _song = catalogue

    client.get(f"/console/artists/{artist.id}", follow_redirects=False)
    first = sum(reads.values())
    reads.clear()
    client.get(f"/console/artists/{artist.id}", follow_redirects=False)

    assert sum(reads.values()) == first, "a second request must read again, not reuse"


def test_a_refusal_is_never_remembered(container, principal, catalogue, monkeypatch):
    """A 403 is a fact about the storage right now, not about the key. Caching one would
    make a request keep reporting failure after the cap had reset."""

    class Refusal(Exception):
        error_code = type("Code", (), {"name": "ACCESS_DENIED"})()

    calls = [0]

    def refuse(_key):
        calls[0] += 1
        raise Refusal()

    monkeypatch.setattr(container.storage, "get", refuse)

    with container.repo.request_scope():
        for _ in range(3):
            with pytest.raises(Refusal):
                container.repo._read("remixkit/tenants/t/songs/s.yaml")

    assert calls[0] == 3, "a refusal must be retried, not cached"


def test_a_miss_is_remembered_because_absence_is_an_answer(container, principal):
    """Asking again for a document that was not there costs exactly what asking for one
    that was costs."""
    calls = [0]

    class Storage:
        def get(self, key):
            calls[0] += 1
            raise FileNotFoundError(key)

    from remixkit.adapters.repo_documents import DocumentRepo

    repo = DocumentRepo(Storage())
    with repo.request_scope():
        for _ in range(3):
            assert repo._read("remixkit/tenants/t/songs/s.yaml") is None

    assert calls[0] == 1


def test_reads_go_straight_through_outside_a_request(container, principal, reads, catalogue):
    """The worker and the CLI have no request boundary, and a long-lived process holding a
    snapshot forever would serve stale documents between jobs."""
    artist, _song = catalogue
    reads.clear()

    container.artists.get(principal, artist.id)
    container.artists.get(principal, artist.id)

    assert sum(reads.values()) == 2, "no scope means no caching"


# ------------------------------------------------------------ dev against prod storage
def test_a_dev_process_on_b2_says_so_out_loud(container, monkeypatch):
    """B2's caps are per *account*, so a laptop browsing the production bucket spends the
    deployed app's daily transactions — and when they run out every read 403s for
    everybody at once. That is not a tidiness problem; it is how the console went down."""
    monkeypatch.setattr(container.settings, "env", "dev")
    monkeypatch.setattr(container.settings, "storage_backend", "b2")
    monkeypatch.setattr(container.settings, "b2_bucket", "respect-the-funk")

    warnings = container.describe()["warnings"]

    assert any("PRODUCTION storage" in w for w in warnings), warnings
    assert any("RK_STORAGE_BACKEND=local" in w for w in warnings), "say what to do about it"


def test_a_deployment_on_b2_is_not_warned_about(container, monkeypatch):
    """Prod reading prod is the point. A warning that fires in the normal case is noise
    that teaches people to skip the banner."""
    monkeypatch.setattr(container.settings, "env", "prod")
    monkeypatch.setattr(container.settings, "storage_backend", "b2")

    assert not any("PRODUCTION storage" in w for w in container.describe()["warnings"])
