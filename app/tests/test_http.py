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
    page = client.get("/")
    assert page.status_code == 200
    assert "Roster" in page.text
    assert "Register an artist" in page.text
    # The environment banner must be on the page, not buried in /healthz.
    assert "MOCKED" in page.text


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
    page = client.get(f"/artists/{artist['id']}")
    assert page.status_code == 200
    assert "Likeness consent" in page.text
    assert "generation is blocked" in page.text
    assert "Identity" in page.text
