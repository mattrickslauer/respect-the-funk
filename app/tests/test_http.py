"""HTTP surface: the JSON API and the htmx console.

The console tests assert on *fragments* — a component route must return just its own
markup, not a whole page, or htmx swaps a nested `<html>` into the DOM. That is the
property that makes these components reusable, so it is worth a test rather than a
convention.
"""

from __future__ import annotations


def test_healthz_names_the_backends(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["generator"] == "genblaze:mock"
    assert body["auth"] == "anonymous"
    # The mock warning must be present — a demo that looks live while it is mocked is
    # a way to mislead a judge by accident.
    assert any("MOCKED" in w for w in body["warnings"])


def test_api_artist_lifecycle(client):
    created = client.post("/api/v1/artists", json={"name": "Nocturnal", "bio": "Synth pop."})
    assert created.status_code == 201
    artist = created.json()
    assert artist["slug"] == "nocturnal"
    assert artist["consent"]["granted"] is False

    assert client.get("/api/v1/artists").json()[0]["id"] == artist["id"]

    consented = client.put(
        f"/api/v1/artists/{artist['id']}/consent",
        json={"granted": True, "signed_by": "A. Tedesco"},
    ).json()
    assert consented["consent"]["granted"] is True
    assert consented["consent"]["signed_at"]


def test_api_kit_is_accepted_not_completed(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    client.put(f"/api/v1/artists/{artist['id']}/consent", json={"granted": True, "signed_by": "A."})
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()

    response = client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1})
    # 202: the work is queued, not done (§2b rule 3).
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_api_refuses_generation_without_consent(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()

    response = client.post("/api/v1/kits", json={"song_id": song["id"]})
    assert response.status_code == 422
    assert "likeness consent" in response.json()["detail"]


def test_api_bpm_without_method_is_refused(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    response = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep", "bpm": 128}
    )
    assert response.status_code == 409
    assert "method" in response.json()["detail"]


def test_console_renders(client):
    page = client.get("/console")
    assert page.status_code == 200
    assert "Roster" in page.text
    assert "Register an artist" in page.text
    # The environment banner must be on the page, not buried in /healthz.
    assert "MOCKED" in page.text


def test_landing_renders_and_offers_a_way_in(client):
    """`/` is the marketing page and carries the only public link to the login."""
    page = client.get("/")
    assert page.status_code == 200
    assert "For independent labels" in page.text
    assert 'href="/auth/login"' in page.text
    # It is the landing page, not the console — none of the roster's machinery is on it.
    # (Matching on copy would not work: the page's own body says "Register an artist
    # once" as a description of the product.)
    assert 'hx-post="/ui/artists"' not in page.text
    assert 'id="roster"' not in page.text


def test_console_fragment_is_a_fragment(client):
    """A component route returns its own markup only — htmx swaps it in place."""
    response = client.post("/ui/artists", data={"name": "Nocturnal", "bio": "", "spotify": "", "instagram": ""})
    assert response.status_code == 200
    assert 'id="roster"' in response.text
    assert "<html" not in response.text.lower()
    assert "Nocturnal" in response.text


def test_console_rights_refusal_renders_as_a_component(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}
    ).json()

    response = client.post(
        "/ui/kits", data={"song_id": song["id"], "artist_id": artist["id"], "video_count": 1}
    )
    assert response.status_code == 422
    assert "likeness consent" in response.text
    assert "<html" not in response.text.lower()


def test_verify_rejects_an_asset_with_no_provenance(client):
    response = client.post(
        "/api/v1/verify", files={"file": ("random.bin", b"not a manifest", "application/octet-stream")}
    )
    body = response.json()
    assert body["verified"] is False
    assert body["source"] == "none"
    assert "no provenance" in body["detail"].lower()


def test_artist_page_shows_the_consent_gate(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    page = client.get(f"/console/artists/{artist['id']}")
    assert page.status_code == 200
    assert "Likeness consent" in page.text
    assert "generation is blocked" in page.text
    assert "Identity" in page.text


def test_the_artist_pages_forms_post_where_they_say_they_do(client):
    """Every fragment route on this page was reachable; the page's own forms were not.

    `artist.html` renders components that address their routes by `artist_id`, and the
    handler passed only `artist` — so the markup went out with `hx-post="/ui/artists//
    songs"`. Each fragment route had a passing test because those tests call the route
    directly with an id, which is exactly the gap this closes: submit what the *page*
    rendered, not what the route accepts.
    """
    import re

    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    page = client.get(f"/console/artists/{artist['id']}").text

    assert "/ui/artists//" not in page, "a form action with an empty path segment"

    actions = re.findall(r'hx-post="(/ui/artists/[^"]+)"', page)
    assert f"/ui/artists/{artist['id']}/songs" in actions
    assert f"/ui/artists/{artist['id']}/identity" in actions

    # And they are live, submitted exactly as the page addresses them.
    assert client.post(
        f"/ui/artists/{artist['id']}/songs", data={"title": "Losing Sleep"}
    ).status_code == 200
    assert client.post(
        f"/ui/artists/{artist['id']}/identity", data={"structural_features": "sharp jaw"}
    ).status_code == 200


def test_the_artist_pages_kit_form_carries_its_artist(client):
    """The hidden `artist_id` decides which kits the response lists back."""
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    # The generate form only renders once there is something to generate from.
    client.post(f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"})
    page = client.get(f"/console/artists/{artist['id']}").text
    assert f'name="artist_id" value="{artist["id"]}"' in page
