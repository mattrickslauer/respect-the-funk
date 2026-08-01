"""The six screens the wireframe specified and the console never had.

`web/wireframe/` describes nine screens; three were built (roster, artist, verifier).
These cover the rest, and in particular the three gaps the wireframe README names as
deliberate — reference frames having no surface, no catalogue view, and cost being
visible only after generating.
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
        domain_song, None, video_count=3
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


def test_what_is_priced_is_what_is_bought(client, container, principal, song):
    """The screen's central claim, asserted end to end rather than per code path.

    It was false once and in a way worth remembering: the page priced a shot the queue
    form did not submit, so the kit that arrived had one shot fewer than the table above
    the button. Both halves read the same inputs now.
    """
    import re

    base = f"/console/artists/{song['artist_id']}/songs/{song['id']}/generate"
    page = client.get(f"{base}?video_count=2").text

    priced_rows = len(re.findall(r'data-shot="\d+"', page))
    # Matched on `data-estimate` rather than on a styling class, so re-laying-out the page
    # cannot break the assertion that the price shown is the price charged.
    shown = re.search(r"data-estimate>\$([0-9.]+)<", page)
    assert shown, "the estimate must be on the page to be compared against"

    client.post(
        "/ui/kits",
        data={
            "song_id": song["id"],
            "artist_id": song["artist_id"],
            "video_count": "2",
        },
    )
    kit = container.kits.list(principal)[0]
    assert kit.brief["shot_count"] == priced_rows
    assert f"{kit.brief['estimate_cents'] / 100:.2f}" == shown.group(1)


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


def test_the_identity_builder_carries_exactly_one_form_for_the_identity(client, artist):
    """It carried two: a hand-rolled one, plus the component the save route returns.

    They disagreed about the field format — the hand-rolled copy asked for wardrobe
    "one per line" while `ui_save_identity` splits on commas, so a multi-line answer
    was saved as a single item. One definition means they cannot disagree again.
    """
    page = client.get(f"/console/artists/{artist['id']}/identity").text

    assert page.count('name="structural_features"') == 1
    assert page.count('name="wardrobe"') == 1
    assert "/ui/artists//" not in page, "a form action with an empty path segment"
    assert f'hx-post="/ui/artists/{artist["id"]}/identity"' in page


def test_the_builders_form_saves_multi_value_fields_as_written(client, container, principal, artist):
    """The format the surviving form asks for is the format the handler parses."""
    client.post(
        f"/ui/artists/{artist['id']}/identity",
        data={
            "structural_features": "high cheekbones",
            "wardrobe": "black leather jacket, silver chain",
            "negatives": "extra fingers, warped face",
        },
    )
    identity = container.identities.current(principal, artist["id"])
    assert identity.wardrobe == ["black leather jacket", "silver chain"]
    assert identity.negatives == ["extra fingers", "warped face"]


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


# ------------------------------------------------------- fragment URL wiring
@pytest.mark.parametrize(
    "path",
    [
        "/console/artists/{artist_id}",
        "/console/artists/{artist_id}/identity",
    ],
)
def test_no_page_renders_a_form_action_with_an_empty_path_segment(client, consented, path):
    """The general form of the `/ui/artists//songs` defect, swept over every page that
    includes a component.

    A page that includes a component owes it every variable the component addresses its
    own routes with, and Jinja renders a missing one as empty rather than raising — so
    the failure is always a page that looks right and a form that 404s. `test_http.py`
    asserts the artist page's specific URLs; this asserts the shape, on every page that
    can grow the same hole. The identity builder is here because it is the other page
    that includes `_identity.html`, and it had the same omission.
    """
    page = client.get(path.format(artist_id=consented["id"]))
    assert page.status_code == 200

    targets = re.findall(r'hx-post="([^"]+)"', page.text)
    assert targets, "expected at least one htmx form on this page"
    for target in targets:
        assert "//" not in target, f"{target} has an empty path segment on {path}"


# ------------------------------------------------------------------ navigation
def test_the_nav_reaches_the_new_screens(client):
    page = client.get("/console")
    assert 'href="/console/catalogue"' in page.text
    assert 'href="/console/settings"' in page.text

