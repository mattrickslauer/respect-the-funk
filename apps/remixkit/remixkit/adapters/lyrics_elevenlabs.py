"""Lyric transcription via ElevenLabs Scribe.

ElevenLabs rather than a new vendor because the project already pays for it and already
carries the key — `bootstrap._PROVIDER_KEYS` has listed `ELEVENLABS_API_KEY` since before
there was anything to transcribe, and `adapters/providers.py` reads it for TTS. The same
argument that put Google behind Veo/Imagen applies here: the cheapest provider to add is
the one already credentialed.

**The provider returns words; a lyric is lines.** Scribe's useful output is a flat list of
words with millisecond timings, and the whole reason to store lines instead is that a line
is the unit a person edits, a kit quotes, and a caption renders. So this adapter groups,
and the grouping rule is deterministic and stated in `method` — three thresholds, applied
in order, no model involved:

    a gap longer than `gap_ms` between two words   → the singer stopped
    a line already at `max_chars` and still going  → it stopped being a line
    a word ending in . ? ! …                       → the sentence closed

That is a heuristic and it is named as one. It can put a break in the wrong place on a
legato vocal, which is why every line is editable and why the boundary it chose is visible
as a timestamp rather than implied by the layout.

**Confidence is derived, not invented.** Scribe reports a log-probability per word; a
line's confidence is the exponential of the mean over its words, which is the geometric
mean of the per-word probabilities. When the provider reports no log-probabilities the
confidence is `None` and stays `None` — see `ports.lyrics` for why a default of 1.0 would
be the one number in this file worth refusing to write.

**A song with no vocal is a result, not a failure.** An instrumental comes back with no
words, and this returns an empty transcript plus a warning saying so. `services.songs`
records that as `done`, because "we listened and there is nothing to hear" is an answer
the console should be able to show instead of an error nobody can act on.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from remixkit.ports.lyrics import TranscribedLine, Transcription
from remixkit.services.errors import TranscriptionUnavailable

log = logging.getLogger(__name__)

# Suffix → what to tell the provider the bytes are. The same closed list
# `services.songs.MASTER_TYPES` accepts, inverted: a master is stored under the extension
# its content type mapped to, so the extension is the reliable side here.
_CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
}

# Sentence-final punctuation, as Scribe emits it. A line break after one of these is the
# only grouping rule here that is about language rather than about time.
_CLOSERS = (".", "?", "!", "…")


class ElevenLabsTranscriber:
    """Scribe, behind `ports.lyrics.Transcriber`.

    Constructed unconditionally by the composition root and reports its own availability,
    for the same reason `NumpyAnalyzer` does: whether the package is installed and whether
    the key is set are two different questions with two different fixes, and the adapter is
    the only place that can tell them apart and say which one is the problem.
    """

    name = "elevenlabs-scribe"

    def __init__(
        self,
        *,
        model_id: str = "scribe_v1",
        api_key: str | None = None,
        gap_ms: int = 900,
        max_chars: int = 42,
        timeout_s: float = 600.0,
    ) -> None:
        self._model_id = model_id
        self._gap_ms = gap_ms
        self._max_chars = max_chars
        self._timeout_s = timeout_s
        self._client: Any = None

        key = (api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
        # The same placeholder check `adapters.providers._keyed` makes, and for the same
        # reason: Terraform seeds every SSM parameter with `PLACEHOLDER …`, so the usual
        # failure is a variable that exists and is not a key. A client built on one of
        # those reports itself available and then 401s inside a queued job.
        if not key or key.startswith("PLACEHOLDER"):
            self.available = False
            self.unavailable_reason = (
                "ELEVENLABS_API_KEY is not set — no transcriber is credentialed in this "
                "process."
            )
            return

        try:
            from elevenlabs.client import ElevenLabs
        except ImportError as exc:
            self.available = False
            self.unavailable_reason = (
                f"the elevenlabs package is not installed ({exc}). "
                "pip install -e '.[lyrics]'"
            )
            return

        self._client = ElevenLabs(api_key=key)
        self.available = True
        self.unavailable_reason = ""

    @property
    def method(self) -> str:
        """The one line stored on the song. Every threshold that shaped the output is in it."""
        return (
            f"elevenlabs {self._model_id}, word timestamps; lines split on a "
            f"{self._gap_ms}ms gap, {self._max_chars} characters, or sentence-final "
            "punctuation; per-line confidence is the geometric mean of word probabilities"
        )

    def transcribe(
        self, data: bytes, *, filename: str = "", language: str | None = None
    ) -> Transcription:
        if not self.available:
            raise TranscriptionUnavailable(self.unavailable_reason)

        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
        content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")

        result = self._client.speech_to_text.convert(
            model_id=self._model_id,
            file=(filename or f"master.{suffix}", data, content_type),
            language_code=(language or None),
            # Off deliberately. `(music)` and `(applause)` markers are useful on a podcast
            # and are noise in a lyric — they would be stored as lines, offered to the
            # generate form as hook lines, and end up on a clip.
            tag_audio_events=False,
            # A master is a mix, not a conversation. Diarisation on a sung vocal over
            # instrumentation splits one singer across several speakers as often as not,
            # and nothing downstream reads speaker ids.
            diarize=False,
            timestamps_granularity="word",
            request_options={"timeout_in_seconds": self._timeout_s},
        )

        words = _words_of(result)
        warnings: list[str] = []
        if not words:
            warnings.append(
                "No words were heard in this master. If it is an instrumental that is the "
                "right answer; if it is not, the vocal may be too far under the mix."
            )

        detected = getattr(result, "language_code", None)
        if language and detected and detected != language:
            warnings.append(
                f"Asked for {language} and the provider heard {detected}. The transcript "
                "is what it heard."
            )

        return Transcription(
            method=self.method,
            lines=self._group(words),
            language=detected or language or None,
            language_confidence=getattr(result, "language_probability", None),
            duration_ms=_duration_ms(result, words),
            warnings=warnings,
        )

    # ------------------------------------------------------------------ grouping
    def _group(self, words: list[Any]) -> list[TranscribedLine]:
        """Flat words → lines, by the three rules in the module docstring.

        `spacing` entries are punctuation and whitespace the provider emits between words;
        they carry no timing worth respecting but their text belongs in the line, so they
        are appended to whatever line is open and never start one.
        """
        lines: list[TranscribedLine] = []
        buffer: list[str] = []
        logprobs: list[float] = []
        start_ms = end_ms = 0
        closed = False

        def flush() -> None:
            nonlocal buffer, logprobs, closed
            text = "".join(buffer).strip()
            if text:
                lines.append(
                    TranscribedLine(
                        text=text,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        confidence=_confidence(logprobs),
                    )
                )
            buffer, logprobs, closed = [], [], False

        for word in words:
            kind = getattr(word, "type", "word") or "word"
            text = getattr(word, "text", "") or ""
            if kind != "word":
                # Punctuation and spacing glue onto the open line, and can close it.
                if buffer:
                    buffer.append(text)
                    if text.strip().endswith(_CLOSERS):
                        closed = True
                continue

            at = _ms(getattr(word, "start", None))
            until = _ms(getattr(word, "end", None))
            pending = "".join(buffer).strip()
            gap = at - end_ms if buffer else 0
            too_long = len(pending) + 1 + len(text) > self._max_chars

            if buffer and (closed or gap > self._gap_ms or too_long):
                flush()

            if not buffer:
                start_ms = at
            elif not buffer[-1].endswith(" ") and not text.startswith(" "):
                buffer.append(" ")

            buffer.append(text)
            end_ms = max(until, at)
            logprob = getattr(word, "logprob", None)
            if logprob is not None:
                logprobs.append(float(logprob))
            if text.strip().endswith(_CLOSERS):
                closed = True

        flush()
        return lines


# ---------------------------------------------------------------- helpers
def _ms(seconds: Any) -> int:
    """Provider seconds → the milliseconds every other window in this app is in."""
    try:
        return max(0, int(round(float(seconds) * 1000)))
    except (TypeError, ValueError):
        return 0


def _confidence(logprobs: list[float]) -> float | None:
    """Geometric mean of the word probabilities, or None when there is nothing to average.

    Clamped to 1.0 at the top because a log-probability of exactly 0 is a certainty and
    floating point can put `exp` a hair above it, and a confidence of 1.0000001 rendered
    as a percentage is the kind of detail that makes a real number look made up.
    """
    if not logprobs:
        return None
    return min(1.0, math.exp(sum(logprobs) / len(logprobs)))


def _words_of(result: Any) -> list[Any]:
    """The word list, whichever response shape came back.

    `use_multi_channel` is not requested, but the SDK's return type is a union and a
    stereo master is exactly the file somebody uploads. Reaching straight for `.words`
    would raise an AttributeError inside a queued job on the one input most likely to
    arrive; taking the first channel is what a single transcript means here.
    """
    words = getattr(result, "words", None)
    if words:
        return list(words)
    transcripts = getattr(result, "transcripts", None) or []
    for transcript in transcripts:
        found = getattr(transcript, "words", None)
        if found:
            return list(found)
    return []


def _duration_ms(result: Any, words: list[Any]) -> int | None:
    """What the provider says the file is, or how far the last word runs. Never a guess.

    The audio duration is the provider's own and is preferred; the last word's end is a
    lower bound and is used only when the provider reported nothing, because a transcript
    whose length is unknown is better than one claiming a length nobody measured.
    """
    reported = getattr(result, "audio_duration_secs", None)
    if reported:
        return _ms(reported)
    if words:
        return max(_ms(getattr(word, "end", None)) for word in words) or None
    return None
