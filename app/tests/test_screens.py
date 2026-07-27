"""The six screens the wireframe specified and the console never had.

`web/wireframe/` describes nine screens; three were built (roster, artist, verifier).
These cover the rest, and in particular the three gaps the wireframe README names as
deliberate — reference frames having no surface, no catalogue view, and cost being
visible only after generating.
"""

from __future__ import annotations

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


# ------------------------------------------------------------------ song
def test_song_page_renders(client, song):
    page = client.get(f"/console/songs/{song['id']}")
    assert page.status_code == 200
    assert "Losing Sleep" in page.text
    assert "Measurement" in page.text and "Hook window" in page.text


def test_song_page_404s_for_a_song_that_is_not_there(client):
    assert client.get("/console/songs/sng_nope").status_code == 404


def test_measurement_saves_and_returns_the_fragment(client, song):
    response = client.post(
        f"/ui/songs/{song['id']}/measurement",
        data={"bpm": "128.5", "bpm_method": "librosa", "drop_ms": "31000"},
    )
    assert response.status_code == 200
    assert "128.50" in response.text and "librosa" in response.text
    assert "<html" not in response.text.lower(), "a fragment route must return a fragment"


def test_a_tempo_without_a_method_is_still_refused_through_the_ui(client, song):
    """The service rule, reached through the new route rather than around it."""
    response = client.post(
        f"/ui/songs/{song['id']}/measurement", data={"bpm": "128", "bpm_method": ""}
    )
    assert response.status_code == 409
    assert "method" in response.text.lower()


def test_a_non_numeric_drop_is_a_refusal_not_a_500(client, song):
    response = client.post(
        f"/ui/songs/{song['id']}/measurement",
        data={"bpm": "128", "bpm_method": "tapped", "drop_ms": "soon"},
    )
    # 409 because it is raised as a Conflict — the point of the test is that a bad
    # field is a refusal the form can render, not an unhandled ValueError.
    assert response.status_code == 409
    assert "whole number" in response.text


def test_hook_window_fragment(client, song):
    response = client.post(
        f"/ui/songs/{song['id']}/hook-window", data={"start_ms": "1000", "end_ms": "7000"}
    )
    assert response.status_code == 200
    assert "6.0s" in response.text


# ------------------------------------------------------------------ catalogue
def test_catalogue_counts_the_gaps(client, song):
    page = client.get("/console/catalogue")
    assert page.status_code == 200
    assert "Losing Sleep" in page.text
    # Fresh song: unmeasured, no hook, no master — but consent was granted.
    assert "measurement" in page.text and "hook window" in page.text


def test_catalogue_service_marks_each_precondition(client, container, principal, song):
    row = container.catalogue.gaps(principal).rows[0]

    assert row.consent_ok is True
    assert row.measured is False and row.has_hook is False and row.has_master is False
    assert row.ready is False
    assert "measurement" in row.blocked_on and "hook window" in row.blocked_on
    assert "likeness consent" not in row.blocked_on


def test_catalogue_counts_artists_not_songs_for_consent(client, container, principal):
    """Two songs by one artist without consent is one decision, not two."""
    artist = client.post("/api/v1/artists", json={"name": "Unconsented"}).json()
    for title in ("A", "B"):
        client.post(f"/api/v1/artists/{artist['id']}/songs", json={"title": title})

    assert container.catalogue.gaps(principal).blocked_artists == 1


def test_catalogue_filters_by_artist(client, song):
    other = client.post("/api/v1/artists", json={"name": "Somebody Else"}).json()
    client.post(f"/api/v1/artists/{other['id']}/songs", json={"title": "Not This One"})

    page = client.get(f"/console/catalogue?artist_id={song['artist_id']}")
    assert "Losing Sleep" in page.text
    assert "Not This One" not in page.text


# ------------------------------------------------------------------ generate
def test_generate_prices_the_plan_before_queueing(client, song):
    page = client.get(
        f"/console/artists/{song['artist_id']}/songs/{song['id']}/generate?video_count=3"
    )
    assert page.status_code == 200
    assert "Queue this kit" in page.text
    assert "Estimate" in page.text
    # The shot table is the plan, not an illustration.
    assert "Vertical 9:16 loop" in page.text or "9:16" in page.text


def test_the_estimate_matches_what_the_service_would_charge(client, container, principal, song):
    """The screen's whole promise: shown cost and bought cost come from one path."""
    from remixkit.services.briefs import default_shot_plan

    domain_song = container.songs.get(principal, song["id"])
    shots = default_shot_plan(
        domain_song, None, video_count=3, hook_lines=[domain_song.title]
    )
    expected = container.kits.estimate_cents(shots)

    page = client.get(
        f"/console/artists/{song['artist_id']}/songs/{song['id']}/generate?video_count=3"
    )
    assert f"${expected / 100:.2f}" in page.text


def test_video_count_changes_the_estimate(client, song):
    base = f"/console/artists/{song['artist_id']}/songs/{song['id']}/generate"
    one = client.get(f"{base}?video_count=1").text
    four = client.get(f"{base}?video_count=4").text
    assert one != four


def test_generate_is_blocked_without_consent(client):
    artist = client.post("/api/v1/artists", json={"name": "No Consent"}).json()
    song = client.post(
        f"/api/v1/artists/{artist['id']}/songs", json={"title": "Blocked"}
    ).json()
    page = client.get(f"/console/artists/{artist['id']}/songs/{song['id']}/generate")
    assert page.status_code == 200
    assert "no recorded likeness consent" in page.text
    assert "disabled" in page.text, "the queue button must not be pressable"


# ------------------------------------------------------------------ kit
def test_kit_page_shows_cost_and_provenance(client, song):
    kit = client.post(
        "/api/v1/kits", json={"song_id": song["id"], "video_count": 1}
    ).json()
    page = client.get(f"/console/kits/{kit['id']}")
    assert page.status_code == 200
    assert "Provenance" in page.text and "Cost ledger" in page.text or "Assets" in page.text


def test_kit_page_404s_for_an_unknown_kit(client):
    assert client.get("/console/kits/kit_nope").status_code == 404


# ------------------------------------------------------------------ identity
def test_identity_page_renders(client, artist):
    page = client.get(f"/console/artists/{artist['id']}/identity")
    assert page.status_code == 200
    assert "Reference frames" in page.text


def test_a_reference_frame_can_finally_be_uploaded(client, container, principal, artist):
    """Wireframe gap #1. `ReferenceFrame` existed on the model with no way to make one."""
    identity = container.identities.create_version(
        principal, artist["id"], structural_features="high cheekbones"
    )

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    response = client.post(
        f"/ui/identities/{identity.id}/frames",
        files={"file": ("face.png", png, "image/png")},
        data={"lighting": "hard-flash", "caption": "press shot"},
    )
    assert response.status_code == 200
    assert "hard-flash" in response.text and "press shot" in response.text

    stored = container.identities.get(principal, identity.id)
    assert len(stored.reference_frames) == 1
    assert stored.reference_frames[0].lighting == "hard-flash"
    # The bytes actually landed, under the tenant-scoped key layout.
    assert container.storage.exists(stored.reference_frames[0].key)
    assert f"/tenants/{principal.tenant_id}/" in stored.reference_frames[0].key


def test_adding_a_frame_does_not_mint_a_version(client, container, principal, artist):
    """Evidence of how someone looks is not a change of intent."""
    identity = container.identities.create_version(principal, artist["id"], structural_features="x")

    client.post(
        f"/ui/identities/{identity.id}/frames",
        files={"file": ("f.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
        data={"lighting": "neutral"},
    )
    assert len(container.identities.list_for_artist(principal, artist["id"])) == 1


def test_an_empty_upload_is_refused(client, container, principal, artist):
    identity = container.identities.create_version(principal, artist["id"], structural_features="x")

    response = client.post(
        f"/ui/identities/{identity.id}/frames",
        files={"file": ("empty.png", b"", "image/png")},
        data={"lighting": "neutral"},
    )
    assert response.status_code == 409


# ------------------------------------------------------------------ settings
def test_settings_page_names_every_axis_and_its_gap(client):
    page = client.get("/console/settings")
    assert page.status_code == 200
    for var in (
        "RK_STORAGE_BACKEND",
        "RK_GENERATOR_BACKEND",
        "RK_QUEUE_BACKEND",
        "RK_MAIL_BACKEND",
        "RK_AUTH_BACKEND",
    ):
        assert var in page.text
    # On the dev defaults it must say what generation would need, not just that it is mocked.
    assert "GMI Cloud" in page.text or "GCP_PROJECT" in page.text


# ------------------------------------------------------------------ navigation
def test_the_nav_reaches_the_new_screens(client):
    page = client.get("/console")
    assert 'href="/console/catalogue"' in page.text
    assert 'href="/console/settings"' in page.text

