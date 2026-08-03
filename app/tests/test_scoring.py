"""The kit plays the record it was cut for.

The failure being tested against is a real one: a kit came back as a stock clip with a
lofi instrumental the model composed itself, over a song it had never heard. Negatives
were the conditioning-signal half of the fix; this is the deterministic half.

The master these tests upload is **silent for its first 30 seconds and a tone after
that**, which is what makes the window assertion real rather than decorative. A clip
scored from the hook at 0:30 is audible; the same clip scored from the top of the record
would be silence, and no amount of "an audio stream exists" would tell the two apart.
"""

from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
import wave

import pytest

from remixkit.domain.models import KitStatus, Modality, SectionRole
from remixkit.services.scoring import ScoringService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="scoring is an ffmpeg round trip; the mock clips need it too",
)

SR = 22_050
TONE_AT_MS = 30_000
MASTER_MS = 60_000


def master_wav() -> bytes:
    """A 60-second master: silence, then a 440 Hz tone from 0:30.

    Built with the standard library rather than with ffmpeg so the fixture is a fact about
    this file and not about the tool under test.
    """
    frames = bytearray()
    for n in range(int(SR * MASTER_MS / 1000)):
        at_ms = n / SR * 1000
        value = (
            int(18_000 * math.sin(2 * math.pi * 440 * n / SR)) if at_ms >= TONE_AT_MS else 0
        )
        frames += struct.pack("<h", value)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def mean_volume_db(data: bytes) -> float:
    """How loud the audio in this MP4 is, per ffmpeg's own measurement.

    `-inf` for a file with no audio at all, which is a different failure from a file whose
    audio is silence and is worth being able to tell apart.
    """
    done = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", "-", "-af", "volumedetect", "-f", "null", "-"],
        input=data,
        capture_output=True,
        timeout=60,
    )
    for line in reversed(done.stderr.decode(errors="replace").splitlines()):
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    return float("-inf")


def has_audio_stream(data: bytes) -> bool:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", "-"],
        input=data,
        capture_output=True,
        timeout=60,
    )
    return b"audio" in done.stdout


# ---------------------------------------------------------------- fixtures
def ready_song(container, principal, *, with_master: bool = True):
    artist = container.artists.create(principal, name="Nocturnal")
    container.artists.set_consent(principal, artist.id, granted=True, signed_by="A. T.")
    song = container.songs.create(principal, artist.id, title="Losing Sleep")
    song = container.songs.set_hook(
        principal, song.id, start_ms=TONE_AT_MS, end_ms=TONE_AT_MS + 6_000
    )
    if with_master:
        upload = container.songs.master_upload_url(
            principal, song.id, content_type="audio/wav"
        )
        container.storage.put(upload["key"], master_wav(), content_type="audio/wav")
        song = container.songs.register_master(
            principal, song.id, key=upload["key"], content_type="audio/wav"
        )
    return artist, song


# ---------------------------------------------------------------- the run
def test_a_kit_clip_carries_the_master(container, principal):
    """The whole point: what plays under the picture is the uploaded record."""
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    assert kit.status is KitStatus.READY, kit.error

    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    assert clip.scored_key, clip.audio_note
    assert clip.audio_source_key == song.master_key
    assert clip.audio_start_ms == TONE_AT_MS

    scored = container.storage.get(clip.scored_key)
    assert has_audio_stream(scored), "the scored cut has no audio track at all"
    # The master is silent until 0:30 and the hook starts there. Anything quieter than
    # this is the top of the record, i.e. the window was ignored.
    assert mean_volume_db(scored) > -40.0

    # And the provider's own bytes are untouched — the manifest hashes those.
    raw = container.storage.get(clip.key)
    assert raw != scored
    assert not has_audio_stream(raw), "the mock clip should have no audio of its own"


def test_the_window_is_the_shots_own_not_the_songs(container, principal):
    """A brief that names two sections deals its loops across them, one window each."""
    _artist, song = ready_song(container, principal)
    song = container.songs.add_section(
        principal, song.id, start_ms=TONE_AT_MS, end_ms=TONE_AT_MS + 6_000,
        role=SectionRole.CHORUS, label="Chorus 1",
    )
    song = container.songs.add_section(
        principal, song.id, start_ms=48_000, end_ms=54_000,
        role=SectionRole.CHORUS, label="Chorus 2",
    )
    # Only the two choruses. `set_hook` above leaves a primary window of its own at the
    # same 0:30, and a brief naming all three would deal both loops to that timestamp.
    section_ids = [s.id for s in song.ordered_sections if s.label.startswith("Chorus")]

    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(
            principal, song_id=song.id, video_count=2, section_ids=section_ids
        ).id,
    )
    assert kit.status is KitStatus.READY, kit.error

    clips = [a for a in kit.assets if a.modality is Modality.VIDEO]
    assert len(clips) == 2
    assert sorted(c.audio_start_ms for c in clips) == [TONE_AT_MS, 48_000]
    assert all(c.scored_key for c in clips)
    # The name the window was bought under travels into the note, so a person reading the
    # kit knows which chorus they are hearing.
    assert any("Chorus 2" in (c.audio_note or "") for c in clips)


def test_a_talking_clip_keeps_its_own_audio(container, principal):
    """A format whose audio *is* the asset is not scored, on either path.

    `direct-address` is generated by instructing a model to speak a line. Scoring maps the
    master in place of the provider's track exhaustively — `-map 0:v:0 -map 1:a:0` — so
    running it here deleted the sentence the label paid to have said and handed back a
    mute performance with the song over it. The clip is delivered as the provider sent it,
    with a note saying why, rather than silently unscored.
    """
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(
            principal,
            song_id=song.id,
            video_count=1,
            recipe_slug="direct-address",
            line="Tickets for the spring run are up now.",
        ).id,
    )
    assert kit.status is KitStatus.READY, kit.error

    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    assert clip.scored_key is None, "the master was laid over a spoken clip"
    assert clip.audio_source_key is None
    assert "own audio is the asset" in (clip.audio_note or "")

    # And the download path does not quietly do it either — that fallback exists for kits
    # the worker did not score, which is exactly the state this clip is deliberately in.
    assert container.scoring.scored_now(principal, kit, clip) == (None, None)


def test_a_song_with_no_master_says_so_on_every_clip(container, principal):
    """The refusal that is the whole diagnosis: there is nothing to lay under this."""
    _artist, song = ready_song(container, principal, with_master=False)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )

    # Still a kit — clips somebody paid for are not thrown away over their audio.
    assert kit.status is KitStatus.READY, kit.error
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    assert clip.scored_key is None
    assert "No master has been uploaded" in clip.audio_note
    assert "Upload the mastered track" in clip.audio_note


def test_scoring_can_be_turned_off_and_says_that_too(container, principal):
    """`RK_SCORE_WITH_MASTER=0` leaves the provider's bytes alone — visibly."""
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    for asset in kit.assets:
        asset.scored_key = None
        asset.audio_note = None

    ScoringService(container.storage, container.songs, enabled=False).score_kit(
        principal, kit, song
    )
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    assert clip.scored_key is None
    assert "RK_SCORE_WITH_MASTER" in clip.audio_note


# ---------------------------------------------------------------- delivery
def test_the_download_hands_back_the_scored_cut(container, principal):
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)

    sent = container.delivery.asset(principal, kit.id, clip.id)
    assert sent.scored is True
    assert has_audio_stream(sent.data)
    assert mean_volume_db(sent.data) > -40.0
    # Provenance still travels inside the file — scoring is a container rewrite, and the
    # embedder has to survive it or the disclosure argument goes with the audio.
    assert sent.manifest_embedded is True, sent.note
    assert container.verify.verify_bytes(sent.data, sent.filename).verified


def test_a_kit_that_ran_before_scoring_is_scored_on_the_way_out(container, principal):
    """An old kit's download is not permanently the model's own soundtrack."""
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    # Rewind the kit to what it would have looked like before this existed.
    container.storage.delete(clip.scored_key)
    clip.scored_key = None
    clip.audio_note = None
    container.repo.put(principal.tenant_id, "kits", kit.id, kit)

    sent = container.delivery.asset(principal, kit.id, clip.id)
    assert sent.scored is True
    assert mean_volume_db(sent.data) > -40.0
    # …and minting it did not quietly write an object the kit has no record of.
    refetched = container.kits.get(principal, kit.id)
    assert refetched.assets[0].scored_key is None


def test_the_api_says_which_audio_it_sent(container, principal, client):
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)

    response = client.get(f"/api/v1/kits/{kit.id}/assets/{clip.id}/download")
    assert response.status_code == 200
    assert response.headers["X-RemixKit-Audio"] == "master"
    assert "The master is under this clip" in response.headers["X-RemixKit-Audio-Note"]


# ---------------------------------------------------------------- the screens
def test_the_plan_says_which_seconds_of_the_record_each_loop_gets(container, principal, client):
    """Before the button, because that is where the 2026-07-31 kit was bought."""
    artist, song = ready_song(container, principal)
    page = client.get(f"/console/artists/{artist.id}/songs/{song.id}/generate")
    assert page.status_code == 200
    assert "laid under the picture" in page.text
    assert "0:30.0" in page.text


def test_the_plan_says_when_there_is_no_master_to_lay(container, principal, client):
    artist, song = ready_song(container, principal, with_master=False)
    page = client.get(f"/console/artists/{artist.id}/songs/{song.id}/generate")
    assert page.status_code == 200
    assert "No master uploaded" in page.text


def test_the_kit_page_says_the_record_is_under_the_clip(container, principal, client):
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    page = client.get(f"/console/kits/{kit.id}")
    assert page.status_code == 200
    assert "master audio" in page.text
    # And the player is not muted, which is the one thing worth checking on this page.
    assert "<video" in page.text and "muted" not in page.text


# ---------------------------------------------------------------- housekeeping
def test_deleting_a_kit_takes_the_scored_copies_with_it(container, principal):
    """Both objects, or the label pays storage for one no screen can reach."""
    _artist, song = ready_song(container, principal)
    kit = container.kits.run(
        principal.tenant_id,
        container.kits.request(principal, song_id=song.id, video_count=1).id,
    )
    clip = next(a for a in kit.assets if a.modality is Modality.VIDEO)
    assert container.storage.exists(clip.scored_key)

    container.kits.delete(principal, kit.id)
    assert not container.storage.exists(clip.scored_key)
    assert not container.storage.exists(clip.key)
