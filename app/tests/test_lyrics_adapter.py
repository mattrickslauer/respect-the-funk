"""The ElevenLabs adapter's own logic, without ElevenLabs.

`FakeTranscriber` covers everything downstream of the port, which means the one part of
this feature it cannot cover is the part that is actually written here: turning a flat list
of words into lines, and turning per-word log-probabilities into a per-line confidence.
Both are deterministic given the provider's output, so they are testable against a stub of
that output and are tested here — the alternative is a heuristic that only ever runs
against a live provider nobody re-runs when they change a threshold.

Nothing in this file makes a network call or needs a credential.
"""

from __future__ import annotations

import math

import pytest

from remixkit.adapters.lyrics_elevenlabs import (
    ElevenLabsTranscriber,
    _confidence,
    _duration_ms,
    _words_of,
)
from remixkit.services.errors import TranscriptionUnavailable


class Word:
    """One entry of the provider's `words` list, in seconds, as the SDK returns it."""

    def __init__(self, text, start, end, *, type="word", logprob=None):
        self.text, self.start, self.end = text, start, end
        self.type, self.logprob = type, logprob


def grouper(**kwargs) -> ElevenLabsTranscriber:
    """An adapter with no key — unavailable, but its grouping is still pure and callable."""
    transcriber = ElevenLabsTranscriber(api_key="", **kwargs)
    assert not transcriber.available
    return transcriber


def test_a_silence_longer_than_the_gap_ends_the_line():
    lines = grouper(gap_ms=900)._group(
        [
            Word("Say", 1.0, 1.2),
            Word("it", 1.2, 1.4),
            # 1.4s of nothing — the singer stopped.
            Word("back", 2.8, 3.0),
        ]
    )

    assert [line.text for line in lines] == ["Say it", "back"]
    assert (lines[0].start_ms, lines[0].end_ms) == (1_000, 1_400)


def test_a_line_breaks_before_it_outgrows_the_width():
    lines = grouper(max_chars=12, gap_ms=10_000)._group(
        [Word(word, i * 0.2, i * 0.2 + 0.1) for i, word in enumerate("alpha bravo charlie".split())]
    )

    assert [line.text for line in lines] == ["alpha bravo", "charlie"]


def test_sentence_final_punctuation_closes_a_line():
    """Punctuation arrives as its own `spacing` entry, glued to the line it closes."""
    lines = grouper(gap_ms=10_000, max_chars=200)._group(
        [
            Word("Stop", 0.0, 0.3),
            Word(".", 0.3, 0.3, type="spacing"),
            Word(" ", 0.3, 0.3, type="spacing"),
            Word("Again", 0.4, 0.8),
        ]
    )

    assert [line.text for line in lines] == ["Stop.", "Again"]


def test_audio_events_never_start_a_line():
    """`tag_audio_events` is off, but a leading marker must not become the first lyric."""
    lines = grouper()._group([Word("(music)", 0.0, 2.0, type="audio_event"), Word("Go", 2.1, 2.4)])

    assert [line.text for line in lines] == ["Go"]


def test_confidence_is_the_geometric_mean_of_the_word_probabilities():
    lines = grouper(gap_ms=10_000, max_chars=200)._group(
        [
            Word("sure", 0.0, 0.2, logprob=math.log(0.9)),
            Word("less", 0.2, 0.4, logprob=math.log(0.5)),
        ]
    )

    assert lines[0].confidence == pytest.approx(math.sqrt(0.9 * 0.5))


def test_a_line_the_provider_gave_no_probabilities_for_has_no_confidence():
    """`None` means "not reported". A default of 1.0 would be a certainty nobody claimed."""
    lines = grouper()._group([Word("unknown", 0.0, 0.3)])

    assert lines[0].confidence is None


def test_confidence_never_exceeds_one():
    assert _confidence([0.0, 0.0]) == 1.0
    assert _confidence([]) is None


def test_words_are_read_off_a_multichannel_response_too():
    """A stereo master is exactly the file somebody uploads; `.words` would be empty."""

    class Channel:
        words = [Word("left", 0.0, 0.2)]

    class MultiChannel:
        words = None
        transcripts = [Channel()]

    assert [word.text for word in _words_of(MultiChannel())] == ["left"]


def test_duration_prefers_the_provider_and_falls_back_to_the_last_word():
    class Reported:
        audio_duration_secs = 214.0

    assert _duration_ms(Reported(), []) == 214_000

    class Silent:
        audio_duration_secs = None

    assert _duration_ms(Silent(), [Word("end", 9.0, 9.5)]) == 9_500
    assert _duration_ms(Silent(), []) is None


def test_an_unkeyed_adapter_refuses_rather_than_returning_nothing():
    """The refusal is the product of this adapter with no key — never an empty transcript."""
    transcriber = ElevenLabsTranscriber(api_key="")

    assert "ELEVENLABS_API_KEY" in transcriber.unavailable_reason
    with pytest.raises(TranscriptionUnavailable):
        transcriber.transcribe(b"audio", filename="master.wav")


def test_a_placeholder_key_is_not_a_key():
    """Terraform seeds every SSM parameter with `PLACEHOLDER …` before anyone has one."""
    assert not ElevenLabsTranscriber(api_key="PLACEHOLDER set me in SSM").available


def test_the_method_line_states_every_threshold_that_shaped_the_output():
    method = grouper(gap_ms=750, max_chars=36).method

    assert "750ms" in method and "36 characters" in method
