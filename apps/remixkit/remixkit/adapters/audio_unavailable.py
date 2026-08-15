"""The analyser for a process that cannot analyse — and why it is not a mock.

Every other port in this app has a zero-credential adapter that *does the thing*: the mock
generator returns real files, local storage stores real bytes, the inline queue runs real
jobs. There is no equivalent for measurement, and the asymmetry is deliberate.

A mocked video is obviously synthetic the moment anybody watches it. A mocked BPM is a
float. It would be written to the song document, rendered next to the word "measured",
carried into the brief of every kit cut from that song, and there is no later point at
which anything or anybody would notice. `services.songs` refuses a tempo without a method
for exactly this reason; an analyser that invents one would be that refusal defeated by
the composition root.

So this adapter refuses, and the refusal names the missing piece. `Container.describe()`
surfaces it in the console footer and on `/healthz`, next to the other honest warnings
about what this deployment is not.
"""

from __future__ import annotations

from remixkit.ports.audio import AudioAnalysis
from remixkit.services.errors import AnalysisUnavailable


class UnavailableAnalyzer:
    name = "unavailable"
    available = False

    def __init__(self, reason: str = "") -> None:
        self.unavailable_reason = reason or (
            "Audio analysis is not available in this process. It needs numpy "
            "(pip install -e '.[audio]') and ffmpeg on PATH for anything but a WAV."
        )

    def analyze(
        self, data: bytes, *, filename: str = "", drop_ms: int | None = None
    ) -> AudioAnalysis:
        raise AnalysisUnavailable(self.unavailable_reason)
