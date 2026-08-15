"""Lyrics — transcribed, then corrected, and never confused about which is which.

The provenance rule this suite exists to hold is `services.songs`': a line the provider
produced and a line a person typed are different claims, and every path that could blur
them — editing text, moving a window, rewriting the lyric wholesale, re-running the
transcriber over corrections — is tested for keeping them apart. The rest is the queued-job
shape analysis already has, which is checked here because it is a second job on the same
document and the two must not collide.
"""

from __future__ import annotations

import pytest

from remixkit.domain.models import AnalysisStatus, Provenance
from remixkit.ports.lyrics import TranscribedLine, Transcription
from remixkit.services.errors import Conflict, NotFound, TranscriptionUnavailable
from remixkit.services.transcription import JOB_TYPE, TranscriptionService

from conftest import FakeTranscriber, sample_transcription


# ---------------------------------------------------------------- the job
def test_request_queues_a_job_and_marks_the_song(container, principal, measured_song):
    song = container.transcription.request(principal, measured_song.id)

    assert song.lyrics.status is AnalysisStatus.QUEUED
    assert song.lyrics.transcriber == "fake-scribe"
    assert {"tenant_id": principal.tenant_id, "song_id": song.id} in container.enqueued


def test_request_without_a_master_is_refused(container, principal):
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="No Master")

    with pytest.raises(Conflict, match="no master to transcribe"):
        container.transcription.request(principal, song.id)


def test_request_without_a_transcriber_is_a_503_naming_the_missing_piece(
    container, principal, measured_song
):
    container.transcription = TranscriptionService(
        container.songs, container.storage, container.queue, FakeTranscriber(available=False)
    )

    with pytest.raises(TranscriptionUnavailable, match="credentialed"):
        container.transcription.request(principal, measured_song.id)

    # Nothing was attempted, so nothing is recorded — the song must not read as `failed`
    # for a job that was never queued.
    assert container.songs.get(principal, measured_song.id).lyrics is None


def test_run_writes_the_lines_with_their_provenance(container, principal, measured_song):
    song = container.transcription.run(principal.tenant_id, measured_song.id)

    assert song.lyrics.status is AnalysisStatus.DONE
    assert song.lyrics.language == "en"
    assert [line.text for line in song.lyrics.ordered_lines] == [
        "Say it back to me",
        "I'm losing sleep again",
        "Something under the mix",
    ]
    assert all(line.source is Provenance.MEASURED for line in song.lyrics.lines)
    assert all(line.method == song.lyrics.method for line in song.lyrics.lines)
    assert song.lyrics.source_sha256 and song.lyrics.source_key == song.master_key


def test_a_failing_provider_records_the_failure_and_keeps_the_previous_lyric(
    container, principal, transcribed_song
):
    container.transcription = TranscriptionService(
        container.songs,
        container.storage,
        container.queue,
        FakeTranscriber(raises=RuntimeError("provider exploded")),
    )
    song = container.transcription.run(principal.tenant_id, transcribed_song.id)

    assert song.lyrics.status is AnalysisStatus.FAILED
    assert "provider exploded" in song.lyrics.error
    # The words that were already there survive an outage nobody caused.
    assert len(song.lyrics.lines) == 3


def test_an_instrumental_is_a_result_not_a_failure(container, principal, measured_song):
    container.transcription = TranscriptionService(
        container.songs,
        container.storage,
        container.queue,
        FakeTranscriber(
            Transcription(method="fake: heard nothing", lines=[], warnings=["No words."])
        ),
    )
    song = container.transcription.run(principal.tenant_id, measured_song.id)

    assert song.lyrics.status is AnalysisStatus.DONE
    assert song.lyrics.lines == []
    assert song.lyrics.warnings == ["No words."]


def test_transcription_and_analysis_do_not_share_a_dedupe_key(
    container, principal, measured_song
):
    """Both jobs carry the same `song_id`, so an unprefixed key would collide."""
    container.transcription.request(principal, measured_song.id)
    container.analysis.request(principal, measured_song.id)

    assert JOB_TYPE != "analyze-song"
    assert len(container.enqueued) == 2


# ---------------------------------------------------------------- editing
def test_editing_a_line_makes_it_manual_and_drops_the_confidence(
    container, principal, transcribed_song
):
    line = transcribed_song.lyrics.ordered_lines[0]
    assert line.confidence == pytest.approx(0.94)

    song = container.songs.update_lyric_line(
        principal, transcribed_song.id, line.id, text="Say it back to me twice"
    )
    edited = song.lyrics.line(line.id)

    assert edited.text == "Say it back to me twice"
    assert edited.source is Provenance.MANUAL
    # The number described the provider's certainty about words it chose. Left attached to
    # a sentence a person wrote it would be a machine's doubt about a human decision.
    assert edited.confidence is None
    assert "hand-edited" in edited.method


def test_moving_a_line_makes_it_manual_even_when_the_words_do_not_change(
    container, principal, transcribed_song
):
    line = transcribed_song.lyrics.ordered_lines[1]
    song = container.songs.update_lyric_line(
        principal, transcribed_song.id, line.id, start_ms=33_500, end_ms=36_900
    )

    assert song.lyrics.line(line.id).source is Provenance.MANUAL
    assert song.lyrics.line(line.id).confidence is None


def test_an_unchanged_save_leaves_the_line_measured(container, principal, transcribed_song):
    """The console posts every field on every save, so a no-op save is the common case."""
    line = transcribed_song.lyrics.ordered_lines[0]
    song = container.songs.update_lyric_line(
        principal,
        transcribed_song.id,
        line.id,
        text=line.text,
        start_ms=line.start_ms,
        end_ms=line.end_ms,
    )

    assert song.lyrics.line(line.id).source is Provenance.MEASURED
    assert song.lyrics.line(line.id).confidence == pytest.approx(0.94)


def test_a_line_can_be_added_to_a_song_that_was_never_transcribed(container, principal):
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="From the Sheet")

    song = container.songs.add_lyric_line(
        principal, song.id, text="Written off the sheet", start_ms=0, end_ms=0
    )

    assert song.lyrics.status is AnalysisStatus.DONE
    assert song.lyrics.ordered_lines[0].source is Provenance.MANUAL
    assert song.lyrics.method is None  # nothing measured it, so nothing claims to have


def test_an_impossible_window_is_refused_but_an_unknown_one_is_not(container, principal):
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="Windows")

    container.songs.add_lyric_line(principal, song.id, text="No timing", start_ms=0, end_ms=0)
    with pytest.raises(Conflict, match="must end after it starts"):
        container.songs.add_lyric_line(
            principal, song.id, text="Backwards", start_ms=9_000, end_ms=1_000
        )


def test_an_empty_line_is_refused(container, principal, transcribed_song):
    with pytest.raises(Conflict, match="needs words"):
        container.songs.add_lyric_line(
            principal, transcribed_song.id, text="   ", start_ms=0, end_ms=0
        )


def test_removing_a_line(container, principal, transcribed_song):
    line = transcribed_song.lyrics.ordered_lines[0]
    song = container.songs.remove_lyric_line(principal, transcribed_song.id, line.id)

    assert len(song.lyrics.lines) == 2
    with pytest.raises(NotFound):
        container.songs.remove_lyric_line(principal, transcribed_song.id, line.id)


# ---------------------------------------------------------------- the bulk edit
def test_bulk_text_keeps_windows_and_only_flips_the_lines_that_changed(
    container, principal, transcribed_song
):
    original = transcribed_song.lyrics.ordered_lines
    song = container.songs.set_lyrics_text(
        principal,
        transcribed_song.id,
        "Say it back to me\nI'm losing sleep again\nSomething underneath the mix",
    )
    lines = song.lyrics.ordered_lines

    assert [line.text for line in lines][2] == "Something underneath the mix"
    # Untouched lines keep the measurement. Marking them manual by association would erase
    # a real provenance because a *different* line was corrected.
    assert lines[0].source is Provenance.MEASURED
    assert lines[1].source is Provenance.MEASURED
    assert lines[2].source is Provenance.MANUAL
    assert lines[2].confidence is None
    # Fixing words does not move when they were sung.
    assert lines[2].start_ms == original[2].start_ms
    assert lines[2].end_ms == original[2].end_ms


def test_bulk_text_warns_when_the_line_count_changes(container, principal, transcribed_song):
    song = container.songs.set_lyrics_text(
        principal, transcribed_song.id, "Say it back to me\nI'm losing sleep again"
    )

    assert len(song.lyrics.lines) == 2
    assert any("line count changed" in w for w in song.lyrics.warnings)


def test_bulk_text_gives_an_extra_line_no_timing_rather_than_a_guess(
    container, principal, transcribed_song
):
    song = container.songs.set_lyrics_text(
        principal,
        transcribed_song.id,
        "Say it back to me\nI'm losing sleep again\nSomething under the mix\nA fourth line",
    )
    added = song.lyrics.ordered_lines[-1]

    assert added.text == "A fourth line"
    assert (added.start_ms, added.end_ms) == (0, 0)
    assert added.source is Provenance.MANUAL


def test_an_untimed_line_stays_where_it_was_put(container, principal, transcribed_song):
    """Sorting untimed lines by `start_ms` would file every one of them before the intro."""
    song = container.songs.add_lyric_line(
        principal, transcribed_song.id, text="After the drop", start_ms=0, end_ms=0
    )
    assert [line.text for line in song.lyrics.ordered_lines][-1] == "After the drop"

    # ...and a lyric with no timings at all keeps the order it was typed in.
    artist = container.artists.create(principal, name="Typed")
    plain = container.songs.create(principal, artist.id, title="From the Sheet")
    for text in ("First", "Second", "Third"):
        plain = container.songs.add_lyric_line(
            principal, plain.id, text=text, start_ms=0, end_ms=0
        )
    assert [line.text for line in plain.lyrics.ordered_lines] == ["First", "Second", "Third"]


def test_bulk_text_ignores_blank_lines(container, principal, transcribed_song):
    song = container.songs.set_lyrics_text(
        principal, transcribed_song.id, "One\n\n   \nTwo\n"
    )
    assert [line.text for line in song.lyrics.ordered_lines] == ["One", "Two"]


# ---------------------------------------------------------------- re-running
def test_a_rerun_over_corrections_is_refused_by_default(
    container, principal, transcribed_song
):
    line = transcribed_song.lyrics.ordered_lines[0]
    container.songs.update_lyric_line(principal, transcribed_song.id, line.id, text="Fixed")

    with pytest.raises(Conflict, match="hand-corrected"):
        container.transcription.request(principal, transcribed_song.id)


def test_a_forced_rerun_replaces_the_lyric_and_says_what_it_discarded(
    container, principal, transcribed_song
):
    line = transcribed_song.lyrics.ordered_lines[0]
    container.songs.update_lyric_line(principal, transcribed_song.id, line.id, text="Fixed")

    container.transcription.request(principal, transcribed_song.id, force=True)
    song = container.transcription.run(principal.tenant_id, transcribed_song.id)

    assert "Fixed" not in [line.text for line in song.lyrics.lines]
    assert all(line.source is Provenance.MEASURED for line in song.lyrics.lines)
    assert any("hand-corrected" in w for w in song.lyrics.warnings)


def test_uncertain_lines_are_the_measured_ones_below_the_threshold(
    container, principal, transcribed_song
):
    uncertain = transcribed_song.lyrics.uncertain_lines

    assert [line.text for line in uncertain] == ["Something under the mix"]
    # A hand-written line has no confidence and is not "uncertain" — nobody is unsure of
    # it, it simply was not measured.
    song = container.songs.add_lyric_line(
        principal, transcribed_song.id, text="Typed", start_ms=0, end_ms=0
    )
    assert len(song.lyrics.uncertain_lines) == 1


def test_lines_in_a_section_window_overlap_rather_than_contain(
    container, principal, transcribed_song
):
    chorus = next(s for s in transcribed_song.ordered_sections if s.label == "Chorus 1")
    inside = transcribed_song.lyrics_for(chorus)

    assert [line.text for line in inside] == ["Say it back to me", "I'm losing sleep again"]

    # A line that starts before the window and runs into it is audible in the cut.
    straddling = sample_transcription(
        lines=[TranscribedLine(text="Over the edge", start_ms=30_000, end_ms=31_000)]
    )
    song = container.songs.apply_transcript(
        principal, transcribed_song.id, straddling, transcriber="fake-scribe"
    )
    assert [line.text for line in song.lyrics_for(chorus)] == ["Over the edge"]


# ---------------------------------------------------------------- API + console
def test_api_transcription_endpoints(client, container, principal, measured_song):
    queued = client.post(f"/api/v1/songs/{measured_song.id}/transcription")
    assert queued.status_code == 202
    assert queued.json()["lyrics"]["status"] == "queued"

    container.transcription.run(principal.tenant_id, measured_song.id)

    lyrics = client.get(f"/api/v1/songs/{measured_song.id}/lyrics").json()
    assert len(lyrics["lines"]) == 3

    text = client.get(f"/api/v1/songs/{measured_song.id}/lyrics.txt")
    assert text.headers["content-type"].startswith("text/plain")
    assert text.text.splitlines()[0] == "Say it back to me"


def test_api_lyrics_text_is_404_when_there_is_no_lyric(client, measured_song):
    """A prompt built around a blank lyric should fail where it is assembled."""
    assert client.get(f"/api/v1/songs/{measured_song.id}/lyrics.txt").status_code == 404


def test_api_line_edits(client, transcribed_song):
    line_id = transcribed_song.lyrics.ordered_lines[0].id

    patched = client.patch(
        f"/api/v1/songs/{transcribed_song.id}/lyrics/lines/{line_id}",
        json={"text": "Say it once"},
    )
    assert patched.status_code == 200
    edited = next(l for l in patched.json()["lyrics"]["lines"] if l["id"] == line_id)
    assert edited["text"] == "Say it once" and edited["source"] == "manual"

    added = client.post(
        f"/api/v1/songs/{transcribed_song.id}/lyrics/lines",
        json={"text": "A new line", "start_ms": 140_000, "end_ms": 142_000},
    )
    assert added.status_code == 201
    assert len(added.json()["lyrics"]["lines"]) == 4

    deleted = client.delete(f"/api/v1/songs/{transcribed_song.id}/lyrics/lines/{line_id}")
    assert len(deleted.json()["lyrics"]["lines"]) == 3


def test_api_rerun_over_corrections_needs_force(client, transcribed_song):
    line_id = transcribed_song.lyrics.ordered_lines[0].id
    client.patch(
        f"/api/v1/songs/{transcribed_song.id}/lyrics/lines/{line_id}",
        json={"text": "Corrected"},
    )

    assert client.post(f"/api/v1/songs/{transcribed_song.id}/transcription").status_code == 409
    forced = client.post(
        f"/api/v1/songs/{transcribed_song.id}/transcription", params={"force": True}
    )
    assert forced.status_code == 202


def test_console_renders_the_lyric_and_its_provenance(client, transcribed_song):
    page = client.get(f"/console/songs/{transcribed_song.id}").text

    assert "Say it back to me" in page
    assert "check by ear" in page          # the low-confidence line is flagged, not hidden
    assert "fake: fixture transcript" in page  # the method is on the page, not in a tooltip


def test_console_line_edit_round_trip(client, transcribed_song):
    line_id = transcribed_song.lyrics.ordered_lines[0].id

    fragment = client.post(
        f"/ui/songs/{transcribed_song.id}/lyrics/lines/{line_id}",
        data={"text": "Say it once", "start_ms": 30_704, "end_ms": 33_100},
    )
    assert fragment.status_code == 200
    assert "Say it once" in fragment.text
    assert "by hand" in fragment.text


def test_console_bulk_edit_round_trip(client, transcribed_song):
    fragment = client.post(
        f"/ui/songs/{transcribed_song.id}/lyrics/text",
        data={"text": "One line\nTwo lines"},
    )
    assert "One line" in fragment.text and "Two lines" in fragment.text


def test_console_rerun_over_corrections_offers_the_override(client, transcribed_song):
    line_id = transcribed_song.lyrics.ordered_lines[0].id
    client.post(
        f"/ui/songs/{transcribed_song.id}/lyrics/lines/{line_id}",
        data={"text": "Corrected", "start_ms": 30_704, "end_ms": 33_100},
    )

    panel = client.post(f"/ui/songs/{transcribed_song.id}/transcription").text
    assert "hand-corrected" in panel
    assert "Re-transcribe anyway" in panel


def test_healthz_names_the_transcriber(client):
    assert "lyrics" in client.get("/healthz").json()
