"""Deleting things, and what deleting them takes with it.

The console could create and read everything but delete almost nothing, which made two
problems invisible for as long as nobody tried.

**Orphaned rows.** `ArtistService.delete` removed the artist and nothing else, so songs
and kits went on referencing an artist that no longer existed — a roster that looks tidy
while `kits.request` 404s on the artist lookup.

**Orphaned bytes.** Every deletable entity owns objects in B2: a song owns an uploaded
master, an identity owns reference frames, a kit owns generated assets and a manifest.
A bucket-as-database has no foreign keys and no cascade, so anything not deleted
deliberately is paid for forever and reachable from no screen.

These tests hold both closed.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import ApprovalState, Asset, KitStatus, Modality
from remixkit.services.errors import Conflict, NotFound


@pytest.fixture
def artist(container, principal):
    """A roster member with likeness consent recorded, so kits are not refused."""
    made = container.artists.create(principal, name="Hallow Youth")
    return container.artists.set_consent(principal, made.id, granted=True, signed_by="mgmt")


@pytest.fixture
def song(container, principal, artist):
    return container.songs.create(principal, artist.id, title="Losing Sleep")


# ------------------------------------------------------------------ collection names
def test_artist_dependents_reads_the_collections_the_services_actually_write():
    """`ArtistService.dependents` uses literal collection names to avoid an import cycle.

    Literals drift silently, so they are asserted against the constants the owning
    services define. If a service renames its collection, this fails rather than the
    dependent count quietly becoming zero — which would turn the refusal below into a
    silent orphaning again.
    """
    from remixkit.services import identities as identities_mod
    from remixkit.services import kits as kits_mod
    from remixkit.services import songs as songs_mod

    assert songs_mod.COLLECTION == "songs"
    assert identities_mod.COLLECTION == "identities"
    assert kits_mod.COLLECTION == "kits"


# ------------------------------------------------------------------ artists
def test_deleting_an_artist_with_dependents_is_refused_and_says_what_is_in_the_way(
    container, principal, artist, song
):
    with pytest.raises(Conflict) as excinfo:
        container.artists.delete(principal, artist.id)

    message = str(excinfo.value)
    assert "song" in message, "the refusal must name what blocks it, not just refuse"
    assert artist.name in message
    # And nothing was removed.
    assert container.artists.get(principal, artist.id)
    assert container.songs.get(principal, song.id)


def test_a_bare_artist_deletes_without_ceremony(container, principal):
    lonely = container.artists.create(principal, name="Nobody Attached")
    container.artists.delete(principal, lonely.id)
    with pytest.raises(NotFound):
        container.artists.get(principal, lonely.id)


def test_cascade_removes_the_dependents_too(container, principal, artist, song):
    container.artists.delete(principal, artist.id, cascade=True)

    with pytest.raises(NotFound):
        container.artists.get(principal, artist.id)
    with pytest.raises(NotFound):
        container.songs.get(principal, song.id)


def test_dependents_counts_each_kind(container, principal, artist, song):
    counts = container.artists.dependents(principal, artist.id)
    assert counts["songs"] == 1
    assert counts["kits"] == 0
    assert set(counts) == {"songs", "identities", "kits"}


# ------------------------------------------------------------------ songs
def test_a_song_can_be_renamed(container, principal, song):
    renamed = container.songs.update(principal, song.id, title="  A Better Title  ")
    assert renamed.title == "A Better Title"
    assert container.songs.get(principal, song.id).title == "A Better Title"


def test_a_song_cannot_be_renamed_to_nothing(container, principal, song):
    with pytest.raises(Conflict):
        container.songs.update(principal, song.id, title="   ")


def test_deleting_a_song_removes_its_master_from_the_bucket(container, principal, song):
    """The master is the largest object this product stores, so it is the most expensive
    thing to orphan."""
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, b"RIFF....fake", content_type="audio/wav")
    container.songs.register_master(principal, song.id, key=key, content_type="audio/wav")
    assert container.storage.exists(key)

    container.songs.delete(principal, song.id)

    assert not container.storage.exists(key), "the master outlived the song it belonged to"
    with pytest.raises(NotFound):
        container.songs.get(principal, song.id)


def test_a_song_with_no_master_still_deletes(container, principal, song):
    container.songs.delete(principal, song.id)
    with pytest.raises(NotFound):
        container.songs.get(principal, song.id)


# ------------------------------------------------------------------ kits
def _ready_kit(container, principal, song, *, approval=ApprovalState.DRAFT):
    """A kit that owns two objects in the bucket, as a finished run would."""
    kit = container.kits.request(principal, song_id=song.id, video_count=1)
    asset_key = f"remixkit/tenants/{principal.tenant_id}/runs/{kit.id}/asset.mp4"
    manifest_key = f"remixkit/tenants/{principal.tenant_id}/runs/{kit.id}/manifest.json"
    container.storage.put(asset_key, b"\x00\x00fake mp4")
    container.storage.put(manifest_key, b"{}")

    kit.status = KitStatus.READY
    kit.approval = approval
    kit.manifest_key = manifest_key
    kit.assets = [
        Asset(modality=Modality.VIDEO, provider="p", model="m", key=asset_key, cost_cents=42)
    ]
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)
    return kit, asset_key, manifest_key


def test_deleting_a_kit_takes_its_assets_and_manifest_out_of_the_bucket(
    container, principal, song
):
    kit, asset_key, manifest_key = _ready_kit(container, principal, song)

    container.kits.delete(principal, kit.id)

    with pytest.raises(NotFound):
        container.kits.get(principal, kit.id)
    assert not container.storage.exists(asset_key), "generated asset outlived its kit"
    assert not container.storage.exists(manifest_key), "manifest outlived its kit"


def test_an_approved_kit_refuses_deletion(container, principal, song):
    """Approval is the record that a human decided this was publishable, and something
    publishable may already be published."""
    kit, _, _ = _ready_kit(container, principal, song, approval=ApprovalState.APPROVED)

    with pytest.raises(Conflict) as excinfo:
        container.kits.delete(principal, kit.id)
    assert "approved" in str(excinfo.value)
    assert container.kits.get(principal, kit.id)


def test_force_deletes_an_approved_kit(container, principal, song):
    kit, asset_key, _ = _ready_kit(container, principal, song, approval=ApprovalState.APPROVED)

    container.kits.delete(principal, kit.id, force=True)

    with pytest.raises(NotFound):
        container.kits.get(principal, kit.id)
    assert not container.storage.exists(asset_key)


def test_a_running_kit_refuses_deletion(container, principal, song):
    """Deleting mid-run leaves the provider generating assets nothing will collect —
    and, since a submitted step is a billed step, paying for them."""
    kit = container.kits.request(principal, song_id=song.id, video_count=1)
    kit.status = KitStatus.RUNNING
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)

    with pytest.raises(Conflict) as excinfo:
        container.kits.delete(principal, kit.id)
    assert "running" in str(excinfo.value)


def test_a_missing_object_does_not_strand_the_kit_row(container, principal, song):
    """A failed kit references keys that were never written. The row must still go."""
    kit = container.kits.request(principal, song_id=song.id, video_count=1)
    kit.status = KitStatus.FAILED
    kit.manifest_key = "remixkit/tenants/t/runs/never-written/manifest.json"
    kit.assets = [
        Asset(modality=Modality.VIDEO, provider="p", model="m", key="also/missing.mp4")
    ]
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)

    container.kits.delete(principal, kit.id)

    with pytest.raises(NotFound):
        container.kits.get(principal, kit.id)


def test_deleting_a_kit_that_does_not_exist_is_a_404_not_a_silent_no_op(container, principal):
    with pytest.raises(NotFound):
        container.kits.delete(principal, "kit_nope")


# ------------------------------------------------------------------ identities
def test_deleting_an_identity_removes_its_reference_frames(container, principal, artist):
    identity = container.identities.create_version(
        principal, artist.id, structural_features="a face", negatives=["blurry"]
    )
    key = f"remixkit/tenants/{principal.tenant_id}/identities/{identity.id}/frame.png"
    container.storage.put(key, b"\x89PNG fake")
    from remixkit.domain.models import ReferenceFrame

    container.identities.add_reference_frame(
        principal, identity.id, ReferenceFrame(key=key, lighting="daylight")
    )
    assert container.storage.exists(key)

    container.identities.delete(principal, identity.id)

    assert not container.storage.exists(key), "reference frame outlived its identity"
    with pytest.raises(NotFound):
        container.identities.get(principal, identity.id)


# ------------------------------------------------------------------ over HTTP
def test_the_api_deletes_a_kit(client, container, principal, song):
    kit, asset_key, _ = _ready_kit(container, principal, song)

    assert client.delete(f"/api/v1/kits/{kit.id}").status_code == 204
    assert client.get(f"/api/v1/kits/{kit.id}").status_code == 404
    assert not container.storage.exists(asset_key)


def test_the_api_refuses_an_approved_kit_with_409(client, container, principal, song):
    kit, _, _ = _ready_kit(container, principal, song, approval=ApprovalState.APPROVED)

    assert client.delete(f"/api/v1/kits/{kit.id}").status_code == 409
    assert client.delete(f"/api/v1/kits/{kit.id}?force=true").status_code == 204


def test_the_api_refuses_to_delete_an_artist_with_dependents(client, artist, song):
    assert client.delete(f"/api/v1/artists/{artist.id}").status_code == 409
    assert client.delete(f"/api/v1/artists/{artist.id}?cascade=true").status_code == 204
    assert client.get(f"/api/v1/artists/{artist.id}").status_code == 404


def test_the_api_reports_dependents_before_asking(client, artist, song):
    body = client.get(f"/api/v1/artists/{artist.id}/dependents").json()
    assert body["songs"] == 1


def test_the_api_refuses_to_delete_a_song_that_kits_were_built_from(
    client, container, principal, song
):
    _ready_kit(container, principal, song)
    assert client.delete(f"/api/v1/songs/{song.id}").status_code == 409


def test_the_api_renames_a_song(client, song):
    got = client.patch(f"/api/v1/songs/{song.id}", json={"title": "Renamed"})
    assert got.status_code == 200
    assert got.json()["title"] == "Renamed"


def test_the_ui_deletes_a_kit_and_returns_the_refreshed_panel(
    client, container, principal, song
):
    kit, _, _ = _ready_kit(container, principal, song)
    got = client.delete(f"/ui/kits/{kit.id}")
    assert got.status_code == 200
    assert kit.name not in got.text, "the deleted kit is still rendered in the panel"


def test_the_ui_shows_the_refusal_rather_than_a_500(client, container, principal, song):
    """A refusal is a fragment the operator reads, not a stack trace."""
    kit, _, _ = _ready_kit(container, principal, song, approval=ApprovalState.APPROVED)
    got = client.delete(f"/ui/kits/{kit.id}")
    assert got.status_code < 500
    assert "approved" in got.text.lower()
