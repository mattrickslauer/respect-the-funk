"""Measuring a master — the methods from `content/bin`, moved behind the port.

This is not a new attempt at structural segmentation. `content/RHYTHM-STUDY.md` already
measured one song to death and wrote down which methods work on this material and which
do not, and `content/bin/measure_beat.py` + `measure_structure.py` are the implementations
it validated. This adapter is those two tools expressed as a library call, so the console
measures a song exactly the way the study did and gets the same answer.

What the study established, and what is therefore implemented here rather than something
more standard:

* **Tempo is a joint comb fit, not bare autocorrelation.** Autocorrelation gives a solid
  period and a phase that wanders, because a small tempo error accumulates over a track's
  worth of beats. Fitting tempo and phase together only scores high when every predicted
  beat lands on a real transient. (`measure_beat.comb_fit`.)
* **A drop is the largest positive step in low-band energy, not a novelty peak.** Foote
  checkerboard novelty ranked the measured drop 8th–79th, never 1st, because a drop is a
  *re-entry* — the material either side of the breakdown is similar, which is exactly the
  configuration a checkerboard scores low. And the usual cosine SSM L2-normalises away
  loudness, the one dimension a drop is made of. (RHYTHM-STUDY §1.)
* **The bar line comes from the arrangement, not from an accent comb.** On four-on-the-
  floor every beat carries a kick, and the four comb phases came out within 5–9% of each
  other while disagreeing about the winner. A mix changes on bar lines and almost nowhere
  else, so the change points *are* the phase reference: their shared residue mod 4 is the
  bar line. That test can fail, and when it does this adapter says so in `warnings` rather
  than reporting a bar line it did not find. (RHYTHM-STUDY §2.)
* **Section ends are measured on the kick, not on loudness.** A mastered drop is limited
  to within a couple of dB, so RMS barely moves at a section boundary while the low band
  falls off a cliff — 58.8 → 41.6 against a 1.09 dB loudness step on the study's track.
  (RHYTHM-STUDY §4.)

What it will not do is score a section for *quality*. RHYTHM-STUDY §5's finding is that
the song contributes structure, not dynamics: a mastered drop is flat to 1.52 dB, so there
is no intensity curve to read off it. This adapter reports where the sections are and how
much kick each one carries. Which one to cut a kit to is `services.recommendations`, and
that ranking is stated as rules with their basis, not as a taste score with a decimal
point on it.

Requires numpy; requires ffmpeg for anything that is not a PCM WAV. Neither is a hard
dependency of the app, so the container falls back to `audio_unavailable` and the console
says which piece is missing. Install with `pip install -e '.[audio]'`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from remixkit.domain.models import SectionRole
from remixkit.ports.audio import AnalyzedSection, AudioAnalysis
from remixkit.services.errors import AnalysisUnavailable

log = logging.getLogger(__name__)

SR = 22050
HOP = 256
NFFT = 1024
LOW_HZ = 200.0  # the kick band, per measure_structure. Above this a filter sweep still
#                 has content and a breakdown stops being legible as a gap.

# How much audio to look at. 10 minutes covers any single, and bounds the cost of a
# comb fit on a file somebody uploaded by mistake.
WINDOW_S = 600.0

# A step counts as structural when it is this many times the typical bar-to-bar movement
# in the same window. Scaled to the material rather than typed as a constant — the study's
# calibration is one positive example, which makes it triage, not authority.
CLIFF_FACTOR = 3.0

FULL_ENERGY = 0.85  # of the loudest section's kick energy → the same tier as the drop
MID_ENERGY = 0.40   # below this a section reads as a breakdown rather than a verse


class NumpyAnalyzer:
    """The real measurement. One pass over the master, everything derived from it."""

    name = "numpy"

    def __init__(self, *, window_s: float = WINDOW_S) -> None:
        self._window_s = window_s
        self._has_numpy = importlib.util.find_spec("numpy") is not None
        self.available = self._has_numpy
        self.unavailable_reason = (
            ""
            if self.available
            else "Audio analysis needs numpy. Install it with: pip install -e '.[audio]'"
        )

    # -- entry point ------------------------------------------------------------
    def analyze(
        self, data: bytes, *, filename: str = "", drop_ms: int | None = None
    ) -> AudioAnalysis:
        if not self.available:
            raise AnalysisUnavailable(self.unavailable_reason)
        import numpy as np

        samples = _decode(data, filename, self._window_s)
        duration_ms = int(len(samples) / SR * 1000)
        if len(samples) < NFFT * 8:
            raise AnalysisUnavailable(
                "That file decoded to almost no audio. Upload the full master, not a preview."
            )

        warnings: list[str] = []
        env = _onset_envelope(np, samples)
        fps = SR / HOP

        bpm, phase_s = _comb_fit(np, env, fps)
        beat_s = 60.0 / bpm
        beat_times = np.arange(phase_s % beat_s, len(samples) / SR, beat_s)
        beat_times = beat_times[beat_times * SR < len(samples) - NFFT]
        if len(beat_times) < 16:
            raise AnalysisUnavailable(
                "Too few beats to measure. This file is shorter than the analyser's minimum."
            )

        rms, low = _beat_measures(np, samples, beat_times)

        # -- the drop ----------------------------------------------------------
        near_b = None
        if drop_ms is not None:
            near_b = int(round((drop_ms / 1000.0 - float(beat_times[0])) / beat_s))
        drop_b, drop_confident = _find_drop(np, low, near_b)
        if not drop_confident:
            warnings.append(
                "No clear kick re-entry — nothing in this track steps far enough in the "
                "low band to call a drop. Sections are still measured; the bar line is not "
                "anchored to a drop."
            )

        # -- the bar line ------------------------------------------------------
        # Phase-locked to the drop when there is one: RHYTHM-STUDY §2's finding is that
        # the drop IS the downbeat, verified by the residue test rather than assumed.
        anchor_b = drop_b if drop_confident else 0
        phase_offset, confidence = _bar_phase(np, rms, low, anchor_b)
        if confidence is not None and confidence < 0.6:
            warnings.append(
                f"Bar phase is inconclusive — only {confidence:.0%} of the largest "
                "arrangement changes share a residue. Section edges are where the energy "
                "moves; they are not claimed to be bar lines."
            )
        bar_start_b = (anchor_b + phase_offset) % 4

        downbeat_ms = None
        if drop_confident and confidence is not None and confidence >= 0.6 and phase_offset == 0:
            downbeat_ms = int(beat_times[drop_b] * 1000)

        # -- the plateau and the riser ----------------------------------------
        plateau = _plateau_beats(np, low, drop_b) if drop_confident else None
        riser = _riser_beats(np, low, drop_b) if drop_confident else None

        sections = _sections(
            np,
            beat_times=beat_times,
            rms=rms,
            low=low,
            bar_start_b=bar_start_b,
            drop_b=drop_b if drop_confident else None,
        )
        if not sections:
            warnings.append("No section boundaries found — the track's energy never steps.")

        method = _method_line(bpm, drop_confident, drop_ms is not None, len(beat_times))
        return AudioAnalysis(
            method=method,
            duration_ms=duration_ms,
            sections=sections,
            bpm=round(float(bpm), 2),
            beat_ms=round(beat_s * 1000, 2),
            downbeat_ms=downbeat_ms,
            drop_ms=int(beat_times[drop_b] * 1000) if drop_confident else None,
            riser_beats=riser,
            plateau_beats=plateau,
            bar_phase_confidence=confidence,
            warnings=warnings,
        )


def _method_line(bpm: float, drop_confident: bool, drop_stated: bool, beats: int) -> str:
    """The one line that gets stored next to every number this produced.

    It names the algorithm, the sample rate, and — the part that matters — whether a
    person pointed at the drop or the analyser picked it. Those are different claims and
    the song document has to be able to tell them apart later.
    """
    where = (
        "largest low-band step near the stated drop"
        if drop_stated
        else "largest low-band step in the track"
    )
    drop = where if drop_confident else "no drop found"
    return (
        f"audio_numpy: spectral-flux onset envelope @ {SR}Hz/hop {HOP}, "
        f"joint tempo+phase comb fit over {beats} beats → {bpm:.2f} BPM; "
        f"drop = {drop}; bar phase from arrangement-change residue; "
        f"sections at bar resolution on <{LOW_HZ:.0f}Hz energy, "
        f"cliff = {CLIFF_FACTOR:.0f}× median bar-to-bar step"
    )


# ---------------------------------------------------------------- decoding
def _decode(data: bytes, filename: str, window_s: float):
    """Bytes → mono float32 at 22050 Hz.

    ffmpeg when it is on PATH, because it reads everything a label will upload. A plain
    PCM WAV is decoded with the standard library instead, so the one format that needs no
    external tool does not require one — which is what makes the analyser usable on a
    machine with numpy and nothing else.
    """
    import numpy as np

    if shutil.which("ffmpeg"):
        suffix = Path(filename or "master.bin").suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(data)
            handle.flush()
            cmd = [
                "ffmpeg", "-v", "error", "-i", handle.name,
                "-t", str(window_s), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-",
            ]
            done = subprocess.run(cmd, capture_output=True)
        if done.returncode != 0 or not done.stdout:
            raise AnalysisUnavailable(
                "ffmpeg could not decode that file: "
                + (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["no output"])[-1]
            )
        return np.frombuffer(done.stdout, dtype=np.float32)

    try:
        return _decode_wav(np, data, window_s)
    except (wave.Error, EOFError, struct.error) as exc:
        raise AnalysisUnavailable(
            "Decoding this file needs ffmpeg on PATH — only uncompressed WAV can be read "
            f"without it ({exc}). Install ffmpeg, or upload a WAV master."
        ) from exc


def _decode_wav(np, data: bytes, window_s: float):
    """PCM WAV without ffmpeg. Downmix, then resample by linear interpolation.

    Linear interpolation is a poor resampler and it is fine here: everything measured
    downstream lives below 200 Hz or is an onset time, and neither is disturbed by the
    imaging a proper anti-aliased resampler would suppress.
    """
    import io

    with wave.open(io.BytesIO(data), "rb") as handle:
        channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
        frames = handle.readframes(min(handle.getnframes(), int(rate * window_s)))

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise wave.Error(f"{width * 8}-bit samples are not supported")
    raw = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    if width == 1:
        raw = (raw - 128.0) / 128.0
    else:
        raw = raw / float(2 ** (width * 8 - 1))
    if channels > 1:
        raw = raw[: len(raw) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != SR:
        n = int(len(raw) * SR / rate)
        raw = np.interp(np.linspace(0, len(raw) - 1, n), np.arange(len(raw)), raw)
    return raw.astype(np.float32)


# ---------------------------------------------------------------- measures
def _onset_envelope(np, x):
    """Half-wave-rectified spectral flux, per frame. `measure_beat.onset_envelope`."""
    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, NFFT), strides=(x.strides[0] * HOP, x.strides[0])
    ) * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    logmag = np.log1p(mag * 10)
    flux = np.maximum(0, np.diff(logmag, axis=0)).sum(axis=1)
    flux -= flux.mean()
    return flux / (flux.std() + 1e-9)


def _score_comb(np, env, fps, bpm: float):
    """Best (score, phase) for one tempo — a beat comb scored against the onsets."""
    period = 60.0 / bpm * fps
    nbeats = int((len(env) - 1) / period)
    if nbeats < 8:
        return -float("inf"), 0.0
    k = np.arange(nbeats)
    phases = np.arange(0, period, 0.5)
    idx = np.rint(phases[:, None] + k[None, :] * period).astype(int)
    scores = env[np.clip(idx, 0, len(env) - 1)].sum(axis=1) / nbeats
    j = int(np.argmax(scores))
    return float(scores[j]), float(phases[j])


def _comb_fit(np, env, fps: float, lo: float = 70.0, hi: float = 180.0):
    """Joint tempo + phase fit, coarse then fine. Returns (bpm, phase in seconds).

    `measure_beat.comb_fit` sweeps 70–180 BPM at 0.02 over the whole track, which is
    thousands of full-length combs and takes the best part of a minute on a long file.
    Split in two it is the same answer for a fraction of the work: a coarse 0.5 BPM sweep
    over a **60-second window** — short enough that a 0.4% period error has not yet walked
    a whole beat out of alignment, which is precisely why the coarse pass cannot be run
    over the full track — then the study's 0.02 resolution over the whole envelope, in a
    ±0.75 BPM band around the winner.
    """
    coarse_end = min(len(env), int(60.0 * fps))
    window = env[:coarse_end] if coarse_end > int(20.0 * fps) else env

    best = (-float("inf"), lo)
    for bpm in np.arange(lo, hi, 0.5):
        score, _ = _score_comb(np, window, fps, float(bpm))
        if score > best[0]:
            best = (score, float(bpm))

    fine = (-float("inf"), best[1], 0.0)
    for bpm in np.arange(max(lo, best[1] - 0.75), min(hi, best[1] + 0.75), 0.02):
        score, phase = _score_comb(np, env, fps, float(bpm))
        if score > fine[0]:
            fine = (score, float(bpm), phase)
    return fine[1], fine[2] / fps


def _beat_measures(np, x, beat_times):
    """Loudness and kick energy, one value per beat. `measure_structure.beat_measures`.

    Beat-synchronous rather than fixed-window: the question is whether bar 6 differs from
    bar 7, and a window straddling the boundary blurs the edge being looked for.
    """
    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, NFFT), strides=(x.strides[0] * HOP, x.strides[0])
    ) * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    fps = SR / HOP
    nlow = max(1, int(LOW_HZ / (SR / NFFT)))

    rms, low = [], []
    for a, b in zip(beat_times, beat_times[1:]):
        seg = x[int(a * SR) : int(b * SR)].astype(np.float64)
        rms.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9) if seg.size else -99.0)
        fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        low.append(float(mag[fa:fb, :nlow].mean()))
    return np.array(rms), np.array(low)


def _find_drop(np, low, near_b: int | None, radius: int = 24, hold: int = 16, gap: int = 4):
    """The beat where the kick comes back, and whether that is a claim worth making.

    `measure_structure.find_drop` searches near a stated estimate because a track has more
    than one drop and only a person knows which one the release is about. With no estimate
    this searches the whole track — and then has to decide whether it found anything, since
    "the largest step" always exists and in material with no drop it is noise.

    Two conditions, and the second is the one that does the work. The step must be large
    against the typical beat-to-beat movement, *and* the kick must still be there
    afterwards: a drop is a section that starts, not a single loud beat. On the study's
    track the low band goes 0.51 → 52 and stays there for seven bars; on a steady click
    with no drop at all the loudest single step is followed by exactly the level that
    preceded it, and the ratio test refuses it where the step test alone would not.
    """
    steps = np.diff(low)
    if steps.size == 0:
        return 0, False
    if near_b is not None and 0 < near_b < len(low):
        lo, hi = max(1, near_b - radius), min(len(low), near_b + radius)
        window = steps[lo - 1 : hi - 1]
        best = (int(np.argmax(window)) + lo) if window.size else near_b
    else:
        best = int(np.argmax(steps)) + 1

    typical = float(np.median(np.abs(steps))) or 1e-9
    # One bar before, four bars after. The pre-window is short on purpose: it is the tail
    # of the riser, where the bottom has already been filtered out. Widening it to match
    # the post-window pulls in the full-energy section before the breakdown and averages
    # the gap away — which on the study's track is the difference between a ratio of 3.2
    # and one of 1.9, and therefore between finding its drop and reporting none.
    before = low[max(0, best - gap) : best]
    after = low[best : best + hold]
    sustained = (
        after.size > 0
        and before.size > 0
        and float(np.median(after)) > 2.0 * float(np.median(before))
    )
    confident = float(steps[best - 1]) > CLIFF_FACTOR * typical and sustained
    return best, confident


def _bar_phase(np, rms, low, anchor_b: int, span: int = 64, k: int = 6, min_sep: int = 4):
    """Where the bar line is, from where the arrangement changes.

    `measure_structure.change_points` + `bar_phase_from_structure`. The inference, stated
    so it can be argued with: a mix changes its arrangement on bar lines and almost nowhere
    else, so if the largest changes near the anchor share a residue mod 4, that residue is
    the bar phase. Returns (beats from the anchor back to the bar line, confidence) where
    confidence is the fraction of change points sharing the winning residue — and `None`
    when there were too few changes to run the test at all.
    """
    lo, hi = max(1, anchor_b - span), min(len(rms), anchor_b + span)
    if hi - lo < 8:
        return 0, None
    scored = sorted(
        (
            (abs(rms[i] - rms[i - 1]) / 3.0 + abs(low[i] - low[i - 1]) / 10.0, i)
            for i in range(lo, hi)
        ),
        reverse=True,
    )
    picked: list[int] = []
    for _, i in scored:
        if any(abs(i - j) < min_sep for j in picked):
            continue
        picked.append(i)
        if len(picked) >= k:
            break
    if len(picked) < 3:
        return 0, None

    residues = [(i - anchor_b) % 4 for i in picked]
    counts = [residues.count(p) for p in range(4)]
    best = int(np.argmax(counts))
    return (-best) % 4, counts[best] / len(picked)


def _plateau_beats(np, low, drop_b: int, max_bars: int = 16) -> int | None:
    """How long the drop holds full energy, in beats. `measure_structure.plateau_beats`.

    The FIRST cliff, not the biggest: a track has several sections and only the nearest
    boundary bounds this loop.
    """
    bars = []
    for b in range(max_bars):
        chunk = low[drop_b + b * 4 : drop_b + b * 4 + 4]
        if len(chunk) < 4:
            break
        bars.append(float(chunk.mean()))
    if len(bars) < 3:
        return len(bars) * 4 or None
    steps = [bars[b] - bars[b + 1] for b in range(len(bars) - 1)]
    typical = float(np.median(np.abs(steps))) or 1e-9
    for b, step in enumerate(steps):
        if step > CLIFF_FACTOR * typical:
            return (b + 1) * 4
    return len(bars) * 4


def _riser_beats(np, low, drop_b: int, look_back: int = 32) -> int | None:
    """The track's own gain ramp — from where the bottom drops out to the drop itself.

    RHYTHM-STUDY §3: on the study's track this is 8 beats, exactly the format's 2-bar
    pre-roll default, and nothing in the system was checking that they agree. Measured
    here so a song whose riser is 4 bars can be *told* that its pre-roll is wrong.
    """
    lo = max(1, drop_b - look_back)
    if drop_b - lo < 4:
        return None
    window = np.diff(low[lo - 1 : drop_b])
    if window.size == 0:
        return None
    collapse = int(np.argmin(window)) + lo
    riser = drop_b - collapse
    return riser if 1 <= riser <= look_back else None


def _sections(np, *, beat_times, rms, low, bar_start_b: int, drop_b: int | None):
    """Bar-resolution segmentation of the whole track, then a role per segment.

    Bars are phase-locked to the bar line found above, so a segment boundary is a bar line
    rather than wherever a beat index happened to fall. Boundaries are steps in the kick
    band larger than the material's own typical bar-to-bar movement — the same rule
    `plateau_beats` uses, applied across the track instead of forward from the drop.

    Roles are **energy tiers**, and the naming is the only inference here: the loudest tier
    is the chorus material (and the segment that starts at the drop is the drop), the
    middle tier is verse material, the quiet tier is a breakdown unless it is the first or
    last segment. This is a starting point a person edits, which is why every section it
    emits is renameable in the console and carries the features it was named from.
    """
    nbars = (len(low) - bar_start_b) // 4
    if nbars < 3:
        return []
    bar_low = np.array(
        [float(low[bar_start_b + i * 4 : bar_start_b + i * 4 + 4].mean()) for i in range(nbars)]
    )
    bar_rms = np.array(
        [float(rms[bar_start_b + i * 4 : bar_start_b + i * 4 + 4].mean()) for i in range(nbars)]
    )

    steps = np.abs(np.diff(bar_low))
    typical = float(np.median(steps)) or 1e-9
    edges = [0] + [i + 1 for i, s in enumerate(steps) if s > CLIFF_FACTOR * typical] + [nbars]

    drop_bar = None
    if drop_b is not None:
        drop_bar = (drop_b - bar_start_b) // 4
        if 0 < drop_bar < nbars and drop_bar not in edges:
            edges = sorted(set(edges + [drop_bar]))

    # Two bars is the floor: shorter than that is a fill, and a "section" a fan cannot
    # cut to is not a section this product has any use for.
    merged = [edges[0]]
    for edge in edges[1:-1]:
        if edge - merged[-1] >= 2:
            merged.append(edge)
    merged.append(edges[-1])
    if len(merged) < 2:
        return []

    spans = list(zip(merged, merged[1:]))
    energies = [float(bar_low[a:b].mean()) for a, b in spans]
    # Reference is the 90th percentile of *bar* energy, not the loudest section. On the
    # study's track the hottest section is a 12-beat one at 63.8 while the drop and both
    # choruses sit at 54–57; dividing by the maximum pushed those under the full-energy
    # threshold and named the song's own chorus a verse. A percentile over bars is not
    # moved by one short hot passage.
    peak = float(np.percentile(bar_low, 90)) or 1e-9

    def beat_ms(beat_index: int) -> int:
        idx = min(max(beat_index, 0), len(beat_times) - 1)
        return int(float(beat_times[idx]) * 1000)

    out: list[AnalyzedSection] = []
    counters: dict[SectionRole, int] = {}
    for i, ((a, b), energy) in enumerate(zip(spans, energies)):
        ratio = min(energy / peak, 1.0)
        if drop_bar is not None and a == drop_bar:
            role = SectionRole.DROP
        elif ratio >= FULL_ENERGY:
            role = SectionRole.CHORUS
        elif ratio >= MID_ENERGY:
            role = SectionRole.VERSE
        elif i == 0:
            role = SectionRole.INTRO
        elif i == len(spans) - 1:
            role = SectionRole.OUTRO
        else:
            role = SectionRole.BREAKDOWN

        counters[role] = counters.get(role, 0) + 1
        start = bar_start_b + a * 4
        end = bar_start_b + b * 4
        out.append(
            AnalyzedSection(
                role=role,
                start_ms=beat_ms(start),
                end_ms=beat_ms(end),
                label=f"{role.value.title()} {counters[role]}",
                beats=(b - a) * 4,
                energy_low_band=round(energy, 3),
                energy_rms_db=round(float(bar_rms[a:b].mean()), 2),
            )
        )
    return [s for s in out if s.duration_ms > 0]


def master_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
