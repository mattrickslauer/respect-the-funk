"""Provenance, end to end — generate, deliver, verify.

This is the claim the whole disclosure argument rests on, so it is tested as a loop
rather than as three separate units: an asset delivered by the system, fed back into
`/verify` with nothing else supplied, must identify the run and the models that made
it. If this passes, "disclosure travels inside the file" is a fact rather than a slide.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import KitStatus


@pytest.fixture
def ready_kit(container):
    principal = container.auth._principal
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. Tedesco")
    container.identities.create_version(
        principal, artist.id, structural_features="sharp jawline", wardrobe=["leather jacket"]
    )
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    container.songs.set_hook(principal, song.id, start_ms=30_000, end_ms=36_000)
    kit = container.kits.request(principal, song_id=song.id, video_count=1, hook_lines=["line"])
    kit = container.kits.run(principal.tenant_id, kit.id)
    assert kit.status is KitStatus.READY, kit.error
    return kit


def test_delivered_asset_carries_its_manifest(container, ready_kit):
    principal = container.auth._principal
    video = next(a for a in ready_kit.assets if a.modality.value == "video")

    delivered = container.delivery.asset(principal, ready_kit.id, video.id)
    assert delivered.manifest_embedded is True, delivered.note
    assert delivered.data != container.storage.get(video.key), "the delivered copy must differ"

    report = container.verify.verify_bytes(delivered.data, delivered.filename)
    assert report.verified is True
    assert report.source == "embedded", "the manifest must come out of the file itself"
    assert report.run_id == ready_kit.run_id
    assert report.tenant_id == principal.tenant_id
    assert any(step["model"] for step in report.steps)


def test_delivered_filename_is_header_safe(container, ready_kit):
    """Kit names default to "<title> — kit". An em dash in Content-Disposition is a
    latin-1 encoding error, i.e. a 500 on the download path."""
    principal = container.auth._principal
    delivered = container.delivery.asset(principal, ready_kit.id, ready_kit.assets[0].id)
    delivered.filename.encode("latin-1")  # raises if it ever regresses
    assert "—" not in delivered.filename


def test_stored_asset_alone_has_no_embedded_provenance(container, ready_kit):
    """The stored object stays byte-identical to what the provider returned — that is
    what makes the content hash in the manifest mean anything. Embedding happens on
    delivery, and this asserts the two are genuinely different states."""
    video = next(a for a in ready_kit.assets if a.modality.value == "video")
    report = container.verify.verify_bytes(container.storage.get(video.key), "raw.mp4")
    assert report.verified is False
    assert report.source == "none"


def test_manifest_document_verifies_on_its_own(container, ready_kit):
    raw = container.storage.get(ready_kit.manifest_key)
    report = container.verify.verify_bytes(raw, "manifest.json")
    assert report.verified is True
    assert report.source == "manifest"
    assert report.run_id == ready_kit.run_id


def test_manifest_key_is_a_key_not_a_url(container, ready_kit):
    """Genblaze reports what it wrote as a URL; the repository needs the key back.
    Getting this wrong yields `files/remixkit/…` and every re-fetch 404s."""
    assert not ready_kit.manifest_key.startswith("/")
    assert not ready_kit.manifest_key.startswith("files/")
    assert ready_kit.manifest_key.startswith("remixkit/runs/")
    assert container.storage.exists(ready_kit.manifest_key)
    for asset in ready_kit.assets:
        assert container.storage.exists(asset.key), f"asset key does not resolve: {asset.key}"


def test_download_route_sets_provenance_header(client):
    artist = client.post("/api/v1/artists", json={"name": "Nocturnal"}).json()
    client.put(f"/api/v1/artists/{artist['id']}/consent", json={"granted": True, "signed_by": "A."})
    song = client.post(f"/api/v1/artists/{artist['id']}/songs", json={"title": "Losing Sleep"}).json()
    kit = client.post("/api/v1/kits", json={"song_id": song["id"], "video_count": 1}).json()

    # The test container records rather than runs, so drive the worker explicitly.
    ran = client.post(
        "/api/v1/internal/worker/run-kit",
        json={"tenant_id": "test-label", "kit_id": kit["id"]},
    ).json()
    assert ran["status"] == "ready"

    full = client.get(f"/api/v1/kits/{kit['id']}").json()
    asset_id = full["assets"][0]["id"]
    response = client.get(f"/api/v1/kits/{kit['id']}/assets/{asset_id}/download")
    assert response.status_code == 200
    assert response.headers["x-remixkit-provenance"] == "embedded"
