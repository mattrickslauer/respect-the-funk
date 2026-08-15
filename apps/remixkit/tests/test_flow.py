"""The spine, end to end: artist → consent → identity → song → hook → kit → verify.

This is BUILD-SPEC §12's Layer 0 acceptance ("one end-to-end: song → Genblaze step →
asset + manifest → verify passes") run against the mock provider table. It is a real
`Pipeline`, a real `ObjectStorageSink`, and a real manifest — only the provider is
synthetic, which is exactly the property that makes this test worth running.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import ApprovalState, KitStatus
from remixkit.services.errors import Conflict, RightsError


def test_consent_gate_blocks_generation(container, principal):
    """The refusal that is a product feature, not a validation nag."""
    artist = container.artists.create(principal, name="Nocturnal")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")

    with pytest.raises(RightsError, match="likeness consent"):
        container.kits.request(principal, song_id=song.id)


def test_bpm_requires_a_method(container, principal):
    """FORMAT-SPEC requires the provenance of a measurement, not just the number."""
    artist = container.artists.create(principal, name="Nocturnal")
    with pytest.raises(Conflict, match="method"):
        container.songs.create(principal, artist.id, title="Losing Sleep", bpm=128.0)

    song = container.songs.create(
        principal,
        artist.id,
        title="Losing Sleep",
        bpm=128.0,
        bpm_method="measure_beat.py, onset autocorrelation, 4 bars from the drop",
    )
    assert song.bpm == 128.0
    assert "measure_beat" in song.bpm_method


def test_full_spine(container, principal):
    artists, identities, songs, kits = (
        container.artists,
        container.identities,
        container.songs,
        container.kits,
    )

    artist = artists.create(principal, name="Nocturnal", bio="Late-night synth pop.")
    artist = artists.set_consent(principal, artist.id, granted=True, signed_by="A. Tedesco")
    assert not artist.consent.blocks_generation

    identity = identities.create_version(
        principal,
        artist.id,
        structural_features="sharp jawline, close-cropped dark hair",
        wardrobe=["black leather jacket"],
        negatives=["extra fingers", "warped face"],
    )
    assert identity.version == 1
    assert "leather jacket" in identity.prompt_fragment()

    song = songs.create(
        principal,
        artist.id,
        title="Losing Sleep",
        bpm=128.0,
        bpm_method="measure_beat.py, onset autocorrelation",
    )
    song = songs.set_hook(principal, song.id, start_ms=30_000, end_ms=36_000)
    assert song.hook.duration_ms == 6_000

    kit = kits.request(principal, song_id=song.id, video_count=2)
    assert kit.status is KitStatus.QUEUED
    assert kit.brief["identity_version"] == 1

    # The inline queue runs on a thread; run it synchronously here so the assertion
    # is about generation rather than about timing.
    kit = kits.run(principal.tenant_id, kit.id)

    assert kit.status is KitStatus.READY, kit.error
    assert kit.assets, "the pipeline produced no assets"
    assert kit.manifest_verified is True, "manifest.verify() must gate READY"
    assert kit.total_cost_cents > 0, "the ledger must record what the run cost"
    assert kit.run_id

    # The identity is actually reaching the provider — this is the claim that makes
    # the second kit cheap, so it is asserted rather than assumed.
    assert any("leather jacket" in (a.prompt or "") for a in kit.assets)


def test_kit_cannot_be_approved_before_it_succeeds(container, principal):
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.request(principal, song_id=song.id, video_count=1)

    with pytest.raises(Conflict, match="generated cleanly"):
        container.kits.set_approval(principal, kit.id, ApprovalState.APPROVED)


def test_rerunning_a_ready_kit_is_free(container, principal):
    """§2b rule 4 — redelivery is a no-op, not a second bill."""
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    kit = container.kits.run(
        principal.tenant_id, container.kits.request(principal, song_id=song.id, video_count=1).id
    )
    assert kit.status is KitStatus.READY

    again = container.kits.run(principal.tenant_id, kit.id)
    assert again.total_cost_cents == kit.total_cost_cents
    assert again.run_id == kit.run_id


def test_inline_queue_actually_runs_the_job(inline_container):
    """The default dev path: `request()` returns immediately, a thread does the work."""
    import time

    principal = inline_container.auth._principal
    artist = inline_container.artists.create(principal, name="Nocturnal")
    inline_container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = inline_container.songs.create(principal, artist.id, title="Losing Sleep")

    kit = inline_container.kits.request(principal, song_id=song.id, video_count=1)
    assert kit.status is KitStatus.QUEUED, "the request handler must not block on generation"

    deadline = time.time() + 60
    while time.time() < deadline:
        current = inline_container.kits.get(principal, kit.id)
        if current.status in (KitStatus.READY, KitStatus.FAILED):
            break
        time.sleep(0.25)
    else:
        pytest.fail("the queued kit never reached a terminal state")

    assert current.status is KitStatus.READY, current.error
    assert current.assets


def test_identity_edit_mints_a_version(container, principal):
    artist = container.artists.create(principal, name="Nocturnal")
    container.identities.create_version(principal, artist.id, structural_features="v1 face")
    second = container.identities.create_version(principal, artist.id, structural_features="v2 face")
    assert second.version == 2
    assert container.identities.current(principal, artist.id).version == 2
    assert len(container.identities.list_for_artist(principal, artist.id)) == 2


def test_tenant_scoping_is_real(container, principal):
    """A different tenant must not see this roster."""
    from remixkit.auth.provider import Principal

    container.artists.create(principal, name="Nocturnal")
    other = Principal(tenant_id="someone-else")
    assert container.artists.list(other) == []
    assert len(container.artists.list(principal)) == 1
