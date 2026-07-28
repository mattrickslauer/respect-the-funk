"""Segments — the part of a measurement that is about what a section *is*.

`test_audio_numpy.py` covers the grid: tempo, the drop, the bar line. This file covers
what was added on top of it, and the case it exists for is the one the grid cannot see.

**The verse and the chorus of `texture_wav` carry an identical kick.** Same fundamental,
same gain, same beat, bar after bar. Everything the previous analyser measured about a
section — mean energy below 200 Hz — is by construction the same number for both, so the
old segmenter could not put a boundary between them, could not name one differently from
the other, and had nothing to offer a person choosing between them. The only thing that
changes is a sustained tone in the band a lead sits in.

That is not a contrived case. It is the normal shape of four-on-the-floor: the kick runs
underneath everything and the arrangement happens above it. So the assertions below are
the whole argument for the change, and if `tonal_mid` and the texture boundary stop
working these are the tests that should fail.
"""

from __future__ import annotations

import io
import math
import wave

import pytest

numpy = pytest.importorskip("numpy", reason="audio analysis is the '[audio]' extra")

from remixkit.adapters.audio_numpy import NumpyAnalyzer  # noqa: E402
from remixkit.domain.models import Provenance  # noqa: E402

SR = 44100


def texture_wav(*, bpm: float = 120.0) -> bytes:
    """intro · verse · chorus · breakdown · chorus, where verse and chorus share a kick.

    The lead is two sine partials at 800/1600 Hz — inside 300–3400 Hz, sustained across
    the whole bar, and carrying no energy at all below 200 Hz. A kick-band measurement is
    blind to it by construction, which is the point.
    """
    beat_s = 60.0 / bpm
    plan = [("intro", 8), ("verse", 8), ("chorus", 8), ("breakdown", 4), ("chorus", 8)]
    beats = sum(bars for _, bars in plan) * 4
    samples = numpy.zeros(int(beats * beat_s * SR) + SR)
    hit_t = numpy.arange(int(0.14 * SR)) / SR
    bar_t = numpy.arange(int(beat_s * 4 * SR)) / SR

    beat = 0
    for name, bars in plan:
        for _ in range(bars):
            for _ in range(4):
                start = int(beat * beat_s * SR)
                if name == "intro":
                    hit = 0.25 * numpy.sin(2 * math.pi * 1500 * hit_t) * numpy.exp(-hit_t * 45)
                elif name == "breakdown":
                    hit = 0.20 * numpy.sin(2 * math.pi * 2200 * hit_t) * numpy.exp(-hit_t * 40)
                else:
                    hit = 0.90 * numpy.sin(2 * math.pi * 55 * hit_t) * numpy.exp(-hit_t * 12)
                samples[start : start + len(hit)] += hit
                beat += 1
            if name == "chorus":
                start = int((beat - 4) * beat_s * SR)
                lead = 0.32 * (
                    numpy.sin(2 * math.pi * 800 * bar_t) + 0.5 * numpy.sin(2 * math.pi * 1600 * bar_t)
                )
                samples[start : start + len(lead)] += lead[: len(samples) - start]

    pcm = numpy.clip(samples, -1, 1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


@pytest.fixture(scope="module")
def measured():
    return NumpyAnalyzer().analyze(texture_wav(), filename="texture.wav")


# ---------------------------------------------------------------- the bar vector
def test_every_bar_is_measured_as_a_vector(measured):
    assert measured.bars, "a measured track has a per-bar curve"
    assert [bar.index for bar in measured.bars] == list(range(len(measured.bars)))
    assert all(bar.start_ms >= 0 for bar in measured.bars)


def test_bands_are_normalised_to_the_track_not_to_the_file(measured):
    """Every band is a share of the track's own 95th-percentile bar, so the numbers are
    comparable down the song and meaningless between two songs — which is the only claim
    the normalisation supports and the reason it is stated in the port."""
    for bar in measured.bars:
        for value in (bar.sub, bar.low_mid, bar.presence, bar.air):
            assert 0.0 <= value <= 1.5
    assert max(bar.total for bar in measured.bars) > 0.5, "some bar is near the top of the scale"


def test_the_lead_is_visible_in_the_band_a_lead_sits_in(measured):
    """The one measurement that separates this track's verse from its chorus."""
    lead_bars = [b for b in measured.bars if b.tonal_mid > 0.5 and b.sub > 0.3]
    kick_only = [b for b in measured.bars if b.tonal_mid < 0.2 and b.sub > 0.3]
    assert lead_bars, "the chorus bars carry tonal energy in 300–3400 Hz"
    assert kick_only, "the verse bars do not"


# ---------------------------------------------------------------- segmentation
def test_a_texture_change_under_an_unchanged_kick_is_a_boundary(measured):
    """The headline claim. The verse and the chorus have the same kick, and the analyser
    still puts a boundary between them — which the kick-band cliff alone cannot do."""
    quiet = {"intro", "breakdown", "outro"}
    loud = [s for s in measured.sections if s.role.value not in quiet]
    assert len(loud) >= 2, "the full-energy stretch is not one undivided block"

    tonal = sorted(s.tonal_mid for s in loud if s.tonal_mid is not None)
    assert tonal[-1] - tonal[0] > 0.3, "and the two differ in the band that separates them"


def test_the_same_material_gets_the_same_type_and_names_its_first_instance(measured):
    types = [s.segment_type for s in measured.sections]
    assert all(t is not None for t in types)

    repeated = [t for t in set(types) if types.count(t) > 1]
    assert repeated, "this arrangement repeats — the intro's material returns as a breakdown"

    for letter in repeated:
        instances = [s for s in measured.sections if s.segment_type == letter]
        assert instances[0].repeat_of is None, "the first instance is nobody's repeat"
        assert all(s.repeat_of == instances[0].label for s in instances[1:])


def test_no_section_is_shorter_than_the_two_bar_floor(measured):
    """Including the last one. A one-bar 'section' at the end of the track was offered as
    something a kit could cut to, and no rule downstream would have passed it."""
    for section in measured.sections:
        assert section.bar_end - section.bar_start >= 2, section.label


def test_a_boundary_says_what_changed_at_it(measured):
    for section in measured.sections:
        assert section.entry, f"{section.label} has no entry description"
    assert any("kick enters" in s.entry for s in measured.sections)


def test_every_role_carries_the_evidence_it_was_named_from(measured):
    for section in measured.sections:
        assert section.evidence, f"{section.label} was named from nothing"
        assert any("type" in line for line in section.evidence)


def test_a_steady_track_is_not_chopped_into_pieces():
    """The failure the absolute floors exist to prevent.

    Nothing happens in this file after the first bar, so the median bar-to-bar movement is
    nearly zero and a rule of `3× median` alone admits numerical noise as a boundary. The
    old behaviour cut a steady stretch into sections whose own entry line read "no large
    level change" — the analyser reporting that it cut where nothing happened.
    """
    from tests.test_audio_numpy import synth_wav

    result = NumpyAnalyzer().analyze(synth_wav(bpm=120.0, bars_quiet=0, bars_loud=16))
    assert len(result.sections) <= 2, [s.label for s in result.sections]


# ---------------------------------------------------------------- the sheet
def test_the_sheet_is_written_and_carries_its_method(measured):
    sheet = measured.sheet
    assert sheet
    assert "ARRANGEMENT" in sheet
    assert measured.method in sheet, "the sheet names how everything in it was measured"
    for section in measured.sections:
        assert section.role.value in sheet


def test_the_sheet_counts_bars_from_one(measured):
    """Bars are 1-based in the sheet and 0-based in the data. A person counting bars out
    loud starts at one, and an off-by-one here is an off-by-one in somebody's edit."""
    first = measured.sections[0]
    assert first.bar_start == 0
    # The span is right-aligned in a fixed column, so the assertion is on the span itself
    # rather than on the padding between it and the word "bars".
    assert f"{first.bar_start + 1}–{first.bar_end}" in measured.sheet
    assert f"{first.bar_start}–{first.bar_end}" not in measured.sheet


def test_a_track_with_no_drop_says_so_in_the_sheet():
    from tests.test_audio_numpy import synth_wav

    result = NumpyAnalyzer().analyze(synth_wav(bpm=120.0, bars_quiet=0, bars_loud=16))
    assert "No drop was found" in result.sheet


# ---------------------------------------------------------------- through the service
def test_the_texture_survives_being_written_to_the_song(container, principal):
    """`apply_analysis` carries every measured field, or the console renders a blank."""
    from remixkit.adapters.audio_numpy import NumpyAnalyzer as Real
    from remixkit.services.analysis import AnalysisService

    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, Real()
    )
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="Texture")
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, texture_wav(), content_type="audio/wav")
    container.songs.register_master(principal, song.id, key=key, content_type="audio/wav")

    song = container.analysis.run(principal.tenant_id, song.id)

    assert song.analysis.bars, "the bar curve is stored, not recomputed per screen"
    assert song.analysis.sheet
    measured_sections = [s for s in song.sections if s.source is Provenance.MEASURED]
    assert measured_sections
    for section in measured_sections:
        assert section.segment_type
        assert section.bar_start is not None
        assert len(section.band_mix) == 4
        assert section.evidence


def test_moving_a_measured_window_by_hand_drops_the_texture_with_the_energies(
    container, principal
):
    """A dragged window keeps no measurement of the audio it used to describe — including
    the evidence sentence, which would otherwise assert a fact about a stretch of the song
    that nobody measured."""
    from remixkit.adapters.audio_numpy import NumpyAnalyzer as Real
    from remixkit.services.analysis import AnalysisService

    container.analysis = AnalysisService(
        container.songs, container.storage, container.queue, Real()
    )
    artist = container.artists.create(principal, name="Hallow Youth")
    song = container.songs.create(principal, artist.id, title="Texture")
    key = f"remixkit/masters/{principal.tenant_id}/{song.id}.wav"
    container.storage.put(key, texture_wav(), content_type="audio/wav")
    container.songs.register_master(principal, song.id, key=key, content_type="audio/wav")
    song = container.analysis.run(principal.tenant_id, song.id)

    target = next(s for s in song.sections if s.source is Provenance.MEASURED)
    song = container.songs.update_section(
        principal, song.id, target.id, start_ms=target.start_ms + 900
    )
    moved = song.section(target.id)

    assert moved.source is Provenance.MANUAL
    assert moved.segment_type is None
    assert moved.tonal_mid is None
    assert moved.band_mix == []
    assert moved.evidence == []
    assert moved.bar_start is None


# ---------------------------------------------------------------- over HTTP
def test_the_sheet_endpoint_serves_text_not_json(client, measured_song):
    """`text/plain` because the consumer is assembling a prompt, and the useful unit there
    is the paragraph the adapter already wrote — not a tree it has to re-render into prose
    itself, which is the step where a claim nobody measured gets introduced."""
    response = client.get(f"/api/v1/songs/{measured_song.id}/sheet")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "ARRANGEMENT" in response.text
    assert measured_song.analysis.method in response.text


def test_an_unmeasured_song_has_no_sheet_and_says_so(client, container, principal):
    """404 rather than an empty body: a prompt built around a blank sheet is a prompt
    about nothing, and it should fail where it is assembled."""
    artist = container.artists.create(principal, name="Nobody")
    song = container.songs.create(principal, artist.id, title="Unmeasured")
    response = client.get(f"/api/v1/songs/{song.id}/sheet")
    assert response.status_code == 404
    assert "has not been measured" in response.json()["detail"]


def test_the_arrangement_fragment_renders_the_measured_song(client, measured_song):
    response = client.get(f"/ui/songs/{measured_song.id}/arrangement")
    assert response.status_code == 200
    assert 'id="song-arrangement"' in response.text
    assert "bandchart" in response.text
    for section in measured_song.ordered_sections:
        assert section.role.value in response.text


def test_the_arrangement_of_an_unmeasured_song_is_an_invitation_not_a_crash(
    client, container, principal
):
    artist = container.artists.create(principal, name="Nobody")
    song = container.songs.create(principal, artist.id, title="Unmeasured")
    response = client.get(f"/ui/songs/{song.id}/arrangement")
    assert response.status_code == 200
    assert "Upload a master" in response.text
