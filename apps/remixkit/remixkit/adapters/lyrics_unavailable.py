"""The transcriber for a process that cannot transcribe — and why it is not a mock.

`adapters/audio_unavailable.py` makes this argument about measurement: a mocked BPM is a
float, indistinguishable from a real one at every point downstream, so there is no mock
analyser in this app. A mocked lyric is worse.

A fabricated measurement is a wrong number. A fabricated lyric is *words attributed to an
artist* — it would be stored on the song, rendered as the song's words, offered to the
generate form as hook lines, and burned into a clip published under the label's name. And
unlike a synthetic video, which announces itself the moment anybody watches it, plausible
lyrics look exactly like real ones to everybody except the person who wrote the song.

So this adapter refuses, and the refusal names the missing piece. `Container.describe()`
surfaces it in the console footer and on `/healthz`, next to the other honest warnings
about what this deployment is not.
"""

from __future__ import annotations

from remixkit.ports.lyrics import Transcription
from remixkit.services.errors import TranscriptionUnavailable


class UnavailableTranscriber:
    name = "unavailable"
    available = False

    def __init__(self, reason: str = "") -> None:
        self.unavailable_reason = reason or (
            "Lyric transcription is not available in this process. It needs the "
            "elevenlabs package (pip install -e '.[lyrics]') and ELEVENLABS_API_KEY set."
        )

    def transcribe(
        self, data: bytes, *, filename: str = "", language: str | None = None
    ) -> Transcription:
        raise TranscriptionUnavailable(self.unavailable_reason)
