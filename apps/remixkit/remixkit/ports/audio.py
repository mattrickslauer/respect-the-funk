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

**Bars are the coordinate system; segments are the content.** The first version of this
port described a song as a beat grid with one number — mean energy below 200 Hz — draped
over it, and every downstream judgement was that number three times: it cut the
boundaries, it named the roles, and it ranked the hooks. Two sections that are
perceptually opposite (a filtered vocal chorus, a bass-heavy instrumental verse) came out
identical or ranked backwards, and nothing that left this port could be read by anything
that does not already speak DSP.

So a section now carries a **texture** as well as a level, a **segment type** saying which
other sections are the same material, and the **evidence** its role was named from; and
the analysis carries a per-bar feature curve plus a `sheet` — the whole arrangement
written out in bars, deterministically, from those measurements. The sheet is the thing an
agent reads. It is assembled by string formatting over measured quantities and no model
writes a word of it, which is what makes it safe to put in a prompt: every sentence in it
is a number this port measured, and there is nowhere for a claim to enter that a person
could not trace back to `method`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from remixkit.domain.models import SectionRole


@dataclass(frozen=True)
class BarFeature:
    """One bar of the song, as a vector. The curve the console draws.

    Band energies are **normalised to this track's own 95th percentile bar**, not left as
    raw magnitudes. Two reasons: a console drawing a stacked bar chart wants 0–1, and a
    raw FFT magnitude is not comparable between two songs mastered by different people, so
    a number that looks cross-comparable and is not would be read as one. Comparison
    across songs is not a claim this port makes.

    `tonal_mid` is the one feature here that is a *proxy* and is named as one. It is the
    tonal (as against noise-like) share of energy in 300–3400 Hz, which is the band a lead
    vocal occupies — high on a sung line, high on a lead synth, low on a drum-only bar. It
    is not a vocal detector and must never be rendered as one.
    """

    index: int          # bar number from the start of the measured grid, 0-based
    start_ms: int
    sub: float          # 20–120 Hz    — the kick's fundamental
    low_mid: float      # 120–500 Hz   — bass line, low body
    presence: float     # 500–4000 Hz  — vocals, leads, snare crack
    air: float          # 4000 Hz+     — hats, cymbals, sheen
    centroid_hz: float  # spectral centroid: where the weight of the spectrum sits
    onsets: float       # onset events in this bar — how busy it is
    rms_db: float
    tonal_mid: float    # 0–1, see above

    @property
    def total(self) -> float:
        """The bar's energy — the stacked height, on the shared scale the bands share."""
        return self.sub + self.low_mid + self.presence + self.air


@dataclass(frozen=True)
class AnalyzedSection:
    """One stretch of the song, as the analyser found it.

    `role` is a name, `evidence` is why. The role remains an inference over energy and
    repetition — not a lyric- or structure-aware classification — and the difference
    between the two is the whole reason `evidence` is on the record rather than in a log
    line: a person disagreeing with "chorus" can see it was named from the top energy tier
    plus three repeats, and a person agreeing can check that it was.
    """

    role: SectionRole
    start_ms: int
    end_ms: int
    label: str = ""
    beats: int | None = None
    energy_low_band: float | None = None
    energy_rms_db: float | None = None

    # -- where it sits on the grid. Bars, because that is the unit everything downstream
    # -- is allowed to cut on, and "bars 33–48" is a thing a person or an agent can say.
    bar_start: int | None = None
    bar_end: int | None = None

    # -- what it is made of ----------------------------------------------------
    band_mix: list[float] = field(default_factory=list)  # sub, low-mid, presence, air
    brightness_hz: float | None = None
    onset_density: float | None = None  # onsets per bar
    tonal_mid: float | None = None

    # -- how it relates to the rest of the song --------------------------------
    segment_type: str | None = None  # "A", "B", … — sections sharing a letter are the
    #                                  same material, by feature distance
    repeat_of: str | None = None     # the label of the first section of this type

    # -- why it is called what it is called ------------------------------------
    evidence: list[str] = field(default_factory=list)
    entry: str = ""  # what changed at this section's first bar

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

    # The per-bar curve every segment above was cut from. Kept rather than discarded
    # because a boundary is an assertion about a shape, and a console that shows the
    # boundary without the shape is asking to be believed instead of checked.
    bars: list[BarFeature] = field(default_factory=list)

    # The arrangement, written out. See the module docstring: formatted from the numbers
    # above, by code, so that everything in it is traceable to `method`.
    sheet: str = ""


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
