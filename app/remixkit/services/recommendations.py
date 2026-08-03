"""Which hook to cut to, and why — computed from the measurement, or not at all.

What this is **not**: a predictor of what performs. Research calls that folklore and
BUILD-SPEC §7 lists it under "what we deliberately do NOT build". Nothing here scores a
section for virality, mood, or appeal, and no number in this module came from a model.

What it is: `content/RHYTHM-STUDY.md` §9's candidate rules, which that study wrote down and
noted are "none of these is enforced today", applied to a measured song. Every rule is
arithmetic over quantities the analyser produced:

    R1  the drop is a bar line          downbeat_ms == drop_ms, by the residue test
    R2  a loop is whole bars, and no longer than the measured plateau
    R3  the pre-roll equals the track's own riser        pre_roll.bars × 4 == riser_beats
    R4  a loop the generator can actually cut            3s ≤ length ≤ 10s

And one product constraint from `services.briefs`: every video in a kit is rendered at the
hook's length, clamped to 3–10 seconds, so a "hook" of 45 seconds is not a hook this system
can render — it is a section somebody has to choose a window inside of.

**Every recommendation carries its basis**, which is the sentence naming the measurement it
rests on. A recommendation with nothing under it is not emitted at a lower confidence; it
is not emitted. When a song has not been analysed, this returns what is missing instead —
which is the useful answer, because the fix is to upload the master, not to guess harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from remixkit.domain.models import Song, SongSection

# The generator's clamp, from `services.briefs._loop_seconds`. Duplicated as named
# constants rather than re-derived, and asserted against in the tests, so a change there
# cannot leave this screen recommending a length the renderer would silently override.
MIN_LOOP_MS = 3_000
MAX_LOOP_MS = 10_000

# How far off a bar line still counts as on it. `measure_beat.py` warns past 25ms, on the
# argument that a fixed offset never washes out — every cut in the sequence stays that late.
BAR_TOLERANCE_MS = 25


@dataclass(frozen=True)
class Check:
    """One rule, applied to one window.

    `ok=None` means **not checkable** — the measurement this rule needs is missing. That is
    a third state on purpose: rendering "unknown" as a failure would tell someone to fix a
    window that may well be correct.
    """

    rule: str
    ok: bool | None
    detail: str

    @property
    def state(self) -> str:
        return "unknown" if self.ok is None else ("pass" if self.ok else "fail")


@dataclass(frozen=True)
class HookCandidate:
    """One section, judged as a loop, with a window that would satisfy the rules."""

    section: SongSection
    checks: list[Check]
    basis: str
    suggested_start_ms: int | None = None
    suggested_end_ms: int | None = None
    rank_reason: str = ""

    @property
    def passes(self) -> int:
        return sum(1 for c in self.checks if c.ok is True)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def ready(self) -> bool:
        """True when the section can be cut as it stands — no failed rule."""
        return not self.failures

    @property
    def has_suggestion(self) -> bool:
        return (
            self.suggested_start_ms is not None
            and self.suggested_end_ms is not None
            and (self.suggested_start_ms, self.suggested_end_ms)
            != (self.section.start_ms, self.section.end_ms)
        )

    @property
    def suggested_duration_ms(self) -> int:
        if self.suggested_start_ms is None or self.suggested_end_ms is None:
            return 0
        return max(0, self.suggested_end_ms - self.suggested_start_ms)


@dataclass(frozen=True)
class Finding:
    """A song-level advisory: something measured that disagrees with something recorded."""

    headline: str
    basis: str
    detail: str = ""


@dataclass(frozen=True)
class Recommendations:
    candidates: list[HookCandidate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    blocked_on: list[str] = field(default_factory=list)

    @property
    def computed(self) -> bool:
        return not self.blocked_on

    @property
    def best(self) -> HookCandidate | None:
        return self.candidates[0] if self.candidates else None


class RecommendationService:
    """Stateless — it reads a song and returns arithmetic. No repository, no I/O."""

    def recommend(self, song: Song) -> Recommendations:
        blocked = _blocked_on(song)
        if blocked:
            return Recommendations(blocked_on=blocked)

        analysis = song.analysis
        beat_ms = analysis.beat_ms if analysis else None
        bar_ms = analysis.bar_ms if analysis else None
        grid_ms, grid_basis = _bar_grid(song)

        candidates = [
            self._judge(section, song, beat_ms=beat_ms, bar_ms=bar_ms, grid_ms=grid_ms,
                        grid_basis=grid_basis)
            for section in song.hook_sections
        ]
        candidates.sort(key=_rank, reverse=True)
        return Recommendations(candidates=candidates, findings=_findings(song))

    # -- one section ------------------------------------------------------------
    def _judge(
        self,
        section: SongSection,
        song: Song,
        *,
        beat_ms: float | None,
        bar_ms: float | None,
        grid_ms: int | None,
        grid_basis: str,
    ) -> HookCandidate:
        analysis = song.analysis
        checks: list[Check] = []

        # R4 — a loop the renderer can actually cut.
        length = section.duration_ms
        checks.append(
            Check(
                rule="renderable loop (3–10s)",
                ok=MIN_LOOP_MS <= length <= MAX_LOOP_MS,
                detail=(
                    f"{length / 1000:.1f}s"
                    + (
                        ""
                        if MIN_LOOP_MS <= length <= MAX_LOOP_MS
                        else f" — every video in a kit renders at the hook's length, clamped to "
                        f"{MIN_LOOP_MS // 1000}–{MAX_LOOP_MS // 1000}s"
                    )
                ),
            )
        )

        # R1 — starts on a bar line. Unknown without a grid, and unknown is not failure.
        if grid_ms is not None and bar_ms:
            offset = _bar_offset(section.start_ms, grid_ms, bar_ms)
            checks.append(
                Check(
                    rule="starts on a bar line",
                    ok=offset <= BAR_TOLERANCE_MS,
                    detail=(
                        f"{offset:.0f}ms off the bar grid ({grid_basis})"
                        + ("" if offset <= BAR_TOLERANCE_MS else " — a fixed offset never washes out")
                    ),
                )
            )
        else:
            checks.append(
                Check("starts on a bar line", None, "no bar line measured on this song")
            )

        # R2a — whole bars.
        if bar_ms:
            bars = length / bar_ms
            off = abs(bars - round(bars)) * bar_ms
            checks.append(
                Check(
                    rule="whole number of bars",
                    ok=off <= BAR_TOLERANCE_MS,
                    detail=f"{bars:.2f} bars"
                    + ("" if off <= BAR_TOLERANCE_MS else " — the loop would end mid-bar"),
                )
            )
        else:
            checks.append(Check("whole number of bars", None, "no tempo measured on this song"))

        # R2b — no longer than the plateau, for the section that starts at the drop.
        plateau = analysis.plateau_beats if analysis else None
        at_drop = (
            analysis is not None
            and analysis.drop_ms is not None
            and abs(section.start_ms - analysis.drop_ms) <= BAR_TOLERANCE_MS
        )
        if at_drop and plateau and beat_ms:
            plateau_ms = plateau * beat_ms
            checks.append(
                Check(
                    rule="inside the measured plateau",
                    ok=length <= plateau_ms + BAR_TOLERANCE_MS,
                    detail=(
                        f"the drop holds full energy for {plateau} beats "
                        f"({plateau_ms / 1000:.1f}s); this window is {length / 1000:.1f}s"
                    ),
                )
            )

        start, end = _suggest_window(
            section, beat_ms=beat_ms, bar_ms=bar_ms, grid_ms=grid_ms,
            plateau_beats=plateau if at_drop else None,
        )
        return HookCandidate(
            section=section,
            checks=checks,
            basis=_basis(section),
            suggested_start_ms=start,
            suggested_end_ms=end,
            rank_reason=_rank_reason(section, song),
        )


# ---------------------------------------------------------------- helpers
def _blocked_on(song: Song) -> list[str]:
    """What is missing before anything can be recommended, in the order to fix it."""
    missing: list[str] = []
    if not song.master_key:
        missing.append("a master to measure — upload the mp3 or wav")
    elif not (song.analysis and song.analysis.ran):
        missing.append("an analysis of the master — nothing has measured this song yet")
    if song.master_key and song.analysis and song.analysis.ran and not song.hook_sections:
        missing.append(
            "a loopable section — the analysis found none, so mark one by hand from what "
            "it did find"
        )
    return missing


def _bar_grid(song: Song) -> tuple[int | None, str]:
    """A millisecond on a bar line, and what makes it one.

    The downbeat when the residue test confirmed one; otherwise the measured drop, since
    RHYTHM-STUDY §2's finding is that the drop *is* the downbeat — stated as the weaker
    claim it is, because that song passed the test and the next one may not.
    """
    analysis = song.analysis
    if not analysis:
        return None, ""
    if analysis.downbeat_ms is not None:
        confidence = analysis.bar_phase_confidence
        suffix = f", {confidence:.0%} of changes agree" if confidence else ""
        return analysis.downbeat_ms, f"downbeat at {analysis.downbeat_ms}ms{suffix}"
    if analysis.drop_ms is not None:
        return analysis.drop_ms, f"drop at {analysis.drop_ms}ms, assumed to be a bar line"
    return None, ""


def _bar_offset(value_ms: int, grid_ms: int, bar_ms: float) -> float:
    """How far `value_ms` sits from the nearest bar line, in ms."""
    rel = (value_ms - grid_ms) % bar_ms
    return min(rel, bar_ms - rel)


def _snap(value_ms: int, grid_ms: int, bar_ms: float) -> int:
    return int(round(grid_ms + round((value_ms - grid_ms) / bar_ms) * bar_ms))


def _suggest_window(
    section: SongSection,
    *,
    beat_ms: float | None,
    bar_ms: float | None,
    grid_ms: int | None,
    plateau_beats: int | None,
) -> tuple[int | None, int | None]:
    """The window this section *should* be cut to, by the rules — or None if unknowable.

    RHYTHM-STUDY §9's generating function, with the renderer's clamp on top:

        start   snapped to the nearest bar line
        length  the largest whole number of bars that fits inside the section, inside the
                measured plateau where there is one, and inside the renderer's 10s ceiling

    §4 is the worked example of why the whole-bars part matters: a 23-beat carousel is
    "the one nearby value" that neither uses the whole plateau nor ends on a bar line.
    """
    if not bar_ms or grid_ms is None:
        return None, None

    start = max(0, _snap(section.start_ms, grid_ms, bar_ms))
    ceiling = section.end_ms - start
    if plateau_beats and beat_ms:
        ceiling = min(ceiling, int(plateau_beats * beat_ms))
    ceiling = min(ceiling, MAX_LOOP_MS)
    bars = int(ceiling // bar_ms)
    if bars < 1:
        return None, None

    length = int(round(bars * bar_ms))
    if length < MIN_LOOP_MS:
        # One bar is under three seconds on anything above 80 BPM, so a loop that must be
        # whole bars and at least 3s takes the next multiple up — and only if the section
        # is long enough to contain it. Otherwise there is no window that satisfies both
        # rules and this says so by declining, rather than by returning a short one.
        needed = -(-MIN_LOOP_MS // int(bar_ms))  # ceil division, in bars
        if start + needed * bar_ms > section.end_ms:
            return None, None
        length = int(round(needed * bar_ms))
    return start, start + length


def _basis(section: SongSection) -> str:
    """The sentence under the recommendation. Never empty — this is the whole discipline."""
    bits: list[str] = []
    if section.energy_low_band is not None:
        bits.append(f"kick energy {section.energy_low_band:.1f}")
    if section.energy_rms_db is not None:
        bits.append(f"{section.energy_rms_db:.1f} dB")
    if section.beats:
        bits.append(f"{section.beats} beats")
    measured = ", ".join(bits)
    if section.method:
        source = section.method.split(":", 1)[0] if measured else section.method
        return f"{measured} — {source}" if measured else source
    return measured or "marked by hand; no measurement behind this window"


def _rank_reason(section: SongSection, song: Song) -> str:
    if section.energy_low_band is None:
        return "hand-marked — ranked below measured sections because nothing measured it"
    analysis = song.analysis
    if analysis and analysis.drop_ms is not None and abs(section.start_ms - analysis.drop_ms) <= BAR_TOLERANCE_MS:
        return "starts at the measured drop"
    return f"kick energy {section.energy_low_band:.1f} across {section.duration_ms / 1000:.1f}s"


def _rank(candidate: HookCandidate) -> tuple:
    """Ordering, not scoring.

    The sort key is (rules passed, measured energy, length) and there is no weighted total
    anywhere — a single number would read as a quality score, which is precisely the claim
    RHYTHM-STUDY §5 says this material cannot support. Sections nobody measured sort below
    sections somebody did, because the energy term is `-1` for them rather than invented.
    """
    section = candidate.section
    return (
        candidate.passes - len(candidate.failures),
        section.energy_low_band if section.energy_low_band is not None else -1.0,
        section.duration_ms,
    )


def _findings(song: Song) -> list[Finding]:
    """Song-level advisories — where a measurement disagrees with what is recorded."""
    analysis = song.analysis
    out: list[Finding] = []
    if not analysis:
        return out

    if analysis.drop_ms is not None and song.drop_ms is not None:
        delta = song.drop_ms - analysis.drop_ms
        if abs(delta) > BAR_TOLERANCE_MS:
            out.append(
                Finding(
                    headline=f"The recorded drop is {delta:+d}ms from the measured kick re-entry",
                    basis=f"largest low-band step at {analysis.drop_ms}ms; the song records {song.drop_ms}ms",
                    detail=(
                        "A fixed offset never washes out — every cut in the sequence stays "
                        "that late. Worth deciding which is right before a kit is cut."
                    ),
                )
            )

    if analysis.riser_beats:
        bars = analysis.riser_beats / 4
        out.append(
            Finding(
                headline=f"The track's own riser is {analysis.riser_beats} beats ({bars:.2g} bars)",
                basis=f"first low-band collapse before the drop at {analysis.drop_ms}ms",
                detail=(
                    "FORMAT-SPEC's pre-roll should equal it — a gain ramp that is not the "
                    "track's own crescendo is a ramp fighting the music (RHYTHM-STUDY §3)."
                ),
            )
        )

    if analysis.plateau_beats:
        bars = analysis.plateau_beats // 4
        out.append(
            Finding(
                headline=f"The drop holds full energy for {analysis.plateau_beats} beats ({bars} bars)",
                basis="first bar whose kick falls past 3× the typical bar-to-bar movement",
                detail="Nothing cut past this point is still cutting to the drop.",
            )
        )

    if analysis.bar_phase_confidence is not None and analysis.bar_phase_confidence < 0.6:
        out.append(
            Finding(
                headline="The bar line is not established for this track",
                basis=f"only {analysis.bar_phase_confidence:.0%} of the largest arrangement changes share a residue",
                detail=(
                    "Alignment rules are reported as unknown rather than failing — this "
                    "method cannot see bar structure in this material."
                ),
            )
        )

    for warning in analysis.warnings:
        out.append(Finding(headline=warning, basis=f"analyser: {analysis.analyzer or 'unknown'}"))
    return out
