"""The audio analysis port.

An uploaded master is the only thing in this system that can answer "where are the hooks"
without a person listening to it. This port is the line between *that question* and the
DSP that answers it: services ask for sections, adapters produce them, and no service
imports numpy.

Two things are deliberately in the interface rather than left to the adapter's discretion.

**`method` is required on the result.** Every analyser must describe how it measured, in
one line, and that line is stored on the song and rendered next to the numbers. This is
`SongService`'s BPM rule applied to the analyser itself: a tool that produces a tempo
without saying how is the same problem as a person typing one.

**`available` / `unavailable_reason` are part of the port.** The real analyser needs
numpy, and mp3 decoding needs ffmpeg; neither is a hard dependency of the app. So the
container always has *an* analyser, and one of them refuses with a message naming the
missing piece. What it must never do is return a plausible BPM it did not measure —
mocking generation produces an obviously synthetic video, but mocking a measurement
produces a number indistinguishable from a real one, and that number would then be
carried into every kit cut from the song.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from remixkit.domain.models import SectionRole


@dataclass(frozen=True)
class AnalyzedSection:
    """One stretch of the song, as the analyser found it.

    `role` is an energy tier named in song vocabulary (see `SectionRole`), not a lyric- or
    structure-aware classification. The features are what it was named from.
    """

    role: SectionRole
    start_ms: int
    end_ms: int
    label: str = ""
    beats: int | None = None
    energy_low_band: float | None = None
    energy_rms_db: float | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class AudioAnalysis:
    """Everything one pass over a master produced.

    Every finding is optional and defaults to `None`, which means **not measured** — never
    zero. An analyser that cannot locate a drop in a track that has no drop must leave
    `drop_ms` unset and say so in `warnings`, because a `0` here would put the drop at the
    first sample of the file and every recommendation downstream would be about that.
    """

    method: str
    duration_ms: int
    sections: list[AnalyzedSection] = field(default_factory=list)
    bpm: float | None = None
    beat_ms: float | None = None
    downbeat_ms: int | None = None
    drop_ms: int | None = None
    riser_beats: int | None = None
    plateau_beats: int | None = None
    bar_phase_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)


class AudioAnalyzer(Protocol):
    name: str
    available: bool
    unavailable_reason: str

    def analyze(
        self, data: bytes, *, filename: str = "", drop_ms: int | None = None
    ) -> AudioAnalysis:
        """Measure a master.

        `drop_ms`, when given, is a person pointing roughly at the drop they mean — a
        track can have several and only a person knows which one the release is about
        (`content/bin/measure_structure.py`, `find_drop`). Without it the analyser picks
        the largest kick re-entry in the track and must say so in its `method`.

        Raises `AnalysisUnavailable` if this analyser cannot run at all.
        """
