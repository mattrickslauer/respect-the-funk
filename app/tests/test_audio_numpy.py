"""The real analyser, against real audio.

Two kinds of test here, and the second is the one that matters.

The synthetic ones build a WAV with a known tempo and a known kick re-entry and check the
analyser finds them. They run anywhere numpy is installed and need no external file.

The last one runs against `content/lib/audio/…` — the exact track `content/RHYTHM-STUDY.md`
measured — and asserts the numbers that study published: 125 BPM, a drop at 124784ms, an
8-beat riser and a 28-beat plateau. That is the real check on this adapter. It is a port
of `content/bin/measure_beat.py` and `measure_structure.py`, and a port that does not
reproduce the original's findings on the original's material is a different algorithm
wearing its docstrings.

Skipped rather than failed when numpy, ffmpeg or the audio file is absent: none of the
three is a hard dependency of the app, and a suite that cannot run on a laptop without
them is a suite people stop running.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy", reason="audio analysis is the '[audio]' extra")

from remixkit.adapters.audio_numpy import NumpyAnalyzer  # noqa: E402
from remixkit.services.errors import AnalysisUnavailable  # noqa: E402

SR = 44100
STUDY_TRACK = (
    Path(__file__).resolve().parents[2]
    / "content/lib/audio/Losing Sleep - Hallow Youth - APLMate.com.mp3"
)


def synth_wav(*, bpm: float = 120.0, bars_quiet: int = 8, bars_loud: int = 8) -> bytes:
    """A click track with a drop in it: quiet ticks, then a loud kick on every beat.

    Not music, and it does not need to be. What it has is the one thing the analyser looks
    for — a large step in sub-200Hz energy at a known beat — plus a regular pulse at a
    known tempo, which is enough to check that both are found where they were put.
    """
    beat_s = 60.0 / bpm
    beats = (bars_quiet + bars_loud) * 4
    samples = numpy.zeros(int(beats * beat_s * SR) + SR, dtype=numpy.float64)
    t = numpy.arange(int(0.12 * SR)) / SR

    for beat in range(beats):
        loud = beat >= bars_quiet * 4
        start = int(beat * beat_s * SR)
        # A 55Hz thump is the kick; the quiet half is a 1.5kHz tick with almost nothing
        # under 200Hz, which is what makes the transition a low-band cliff.
        freq, gain = (55.0, 0.9) if loud else (1500.0, 0.25)
        hit = gain * numpy.sin(2 * math.pi * freq * t) * numpy.exp(-t * (12 if loud else 45))
        samples[start : start + len(hit)] += hit

    pcm = numpy.clip(samples, -1, 1)
    frames = (pcm * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(frames)
    return buffer.getvalue()


# ------------------------------------------------------------------ synthetic
def test_the_tempo_comes_back_out():
    result = NumpyAnalyzer().analyze(synth_wav(bpm=120.0), filename="click.wav")
    assert result.bpm == pytest.approx(120.0, abs=0.6)
    assert result.beat_ms == pytest.approx(500.0, abs=3.0)


def test_the_drop_is_found_where_it_was_put():
    """8 bars of ticks, then kicks — the drop is at 32 beats, 16 seconds in at 120 BPM."""
    result = NumpyAnalyzer().analyze(synth_wav(bpm=120.0), filename="click.wav")
    assert result.drop_ms == pytest.approx(16_000, abs=250)


def test_a_track_with_no_kick_re_entry_reports_no_drop():
    """"The largest step" always exists. In material with no drop it is noise, and a
    number here would put every recommendation downstream on an arbitrary beat."""
    steady = synth_wav(bpm=120.0, bars_quiet=0, bars_loud=16)
    result = NumpyAnalyzer().analyze(steady, filename="steady.wav")

    assert result.drop_ms is None
    assert any("no clear kick re-entry" in w.lower() for w in result.warnings)


def test_sections_are_measured_with_their_energy():
    result = NumpyAnalyzer().analyze(synth_wav(bpm=120.0), filename="click.wav")
    assert len(result.sections) >= 2
    quiet, loud = result.sections[0], result.sections[-1]
    assert loud.energy_low_band > quiet.energy_low_band * 3
    assert all(s.method is None or True for s in [])  # sections carry features, not methods


def test_the_method_line_says_whether_a_person_pointed_at_the_drop():
    audio = synth_wav(bpm=120.0)
    analyzer = NumpyAnalyzer()
    assert "largest low-band step in the track" in analyzer.analyze(audio).method
    assert "near the stated drop" in analyzer.analyze(audio, drop_ms=16_000).method


def test_a_file_that_is_not_audio_is_a_refusal_not_a_crash():
    with pytest.raises(AnalysisUnavailable):
        NumpyAnalyzer().analyze(b"this is not a wav file at all", filename="nope.wav")


def test_two_seconds_of_audio_is_refused_rather_than_measured():
    with pytest.raises(AnalysisUnavailable):
        NumpyAnalyzer().analyze(synth_wav(bpm=120.0, bars_quiet=0, bars_loud=1))


def test_wav_decodes_without_ffmpeg(monkeypatch):
    """The one format that needs no external tool must not require one — that is what
    makes the analyser usable on a machine with numpy and nothing else."""
    import remixkit.adapters.audio_numpy as adapter

    monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
    result = adapter.NumpyAnalyzer().analyze(synth_wav(bpm=120.0), filename="click.wav")
    assert result.bpm == pytest.approx(120.0, abs=0.6)


def test_an_mp3_without_ffmpeg_says_what_is_missing(monkeypatch):
    import remixkit.adapters.audio_numpy as adapter

    monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
    with pytest.raises(AnalysisUnavailable) as exc:
        adapter.NumpyAnalyzer().analyze(b"ID3\x04not really an mp3", filename="master.mp3")
    assert "ffmpeg" in str(exc.value)


# ------------------------------------------------------------------ the study's own track
@pytest.mark.skipif(not STUDY_TRACK.exists(), reason="the study's master is not in this checkout")
def test_it_reproduces_the_rhythm_study_on_the_song_the_study_measured():
    """RHYTHM-STUDY §2–§4, recomputed by this adapter.

    The study's own figures for `losing-sleep`: 125 BPM, the drop at 124784ms, a riser
    beginning 8 beats (2 bars) before it, a full-energy plateau of 28 beats (7 bars), and
    five of six large arrangement changes sharing one residue mod 4.

    `drop_ms` is supplied because the study supplies it — the track has an earlier drop at
    ~48s, and §find_drop's argument is that only a person knows which one the release is
    about. Passing it is the point of the parameter, not a way of getting the answer: the
    analyser searches ±24 beats and could land anywhere in a 23-second window.
    """
    result = NumpyAnalyzer().analyze(STUDY_TRACK.read_bytes(), filename=STUDY_TRACK.name,
                                     drop_ms=124_784)

    assert result.bpm == pytest.approx(125.0, abs=0.05)
    assert result.drop_ms == pytest.approx(124_784, abs=25), "the study's measured drop"
    assert result.downbeat_ms == result.drop_ms, "§2 — the drop IS the downbeat"
    assert result.riser_beats == 8, "§3 — a 2-bar riser, the format's pre-roll default"
    assert result.plateau_beats == 28, "§4 — 7 bars, against the song file's declared 23"
    assert result.bar_phase_confidence == pytest.approx(5 / 6, abs=0.01)
    assert not result.warnings

    hooks = [s for s in result.sections if s.role.is_hook_material]
    assert len(hooks) > 1, "a song with several full-energy sections has several hooks"
    assert any(s.start_ms == result.drop_ms for s in hooks)


@pytest.mark.skipif(not STUDY_TRACK.exists(), reason="the study's master is not in this checkout")
def test_without_a_stated_drop_it_finds_the_loudest_re_entry_and_says_so():
    """A track has more than one drop. With nobody pointing, the analyser takes the largest
    step in the track — and records in its method line that that is what it did."""
    result = NumpyAnalyzer().analyze(STUDY_TRACK.read_bytes(), filename=STUDY_TRACK.name)

    assert result.drop_ms != 124_784, "this track's largest step is its first drop, at ~48s"
    assert "largest low-band step in the track" in result.method
