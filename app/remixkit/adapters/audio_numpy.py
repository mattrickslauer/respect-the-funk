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
is no intensity curve to read off it. This adapter reports where the sections are and what
each one is made of. Which one to cut a kit to is `services.recommendations`, and that
ranking is stated as rules with their basis, not as a taste score with a decimal point on
it.

## What was added after the study, and on whose authority

Everything above is the study's, measured on the study's track. Three things below it are
not, and the distinction matters when reading a disagreement later.

* **A bar is a vector, not a number.** The study needed one band because it was looking for
  a drop, and a drop is made of low-band energy. Naming a *section* needs more: on kick
  energy alone a filtered vocal chorus and a bass-heavy instrumental verse are the same
  reading. So each bar now carries four band energies, a spectral centroid, an onset count,
  and `tonal_mid` — the tonal share of 300–3400 Hz, which is where a lead vocal sits and
  which is a **proxy, not a detector**, named that way everywhere it is rendered.

* **Boundaries are the union of two tests, not a replacement.** The low-band cliff the
  study validated still cuts every boundary it used to. A second test cuts where the
  *whole* feature vector steps — which is how a section that changes texture while the
  kick keeps going stops being invisible. Union rather than replacement is deliberate: the
  study's boundaries are the ones with a validated positive example behind them, and this
  adapter is not entitled to remove them on the strength of a method nobody has tested on
  this material.

* **Repetition is a different question from novelty, and Foote is good at it.** §1 rules
  out checkerboard novelty for finding a *drop*, and the reason it gives — the material
  either side of a breakdown is similar — is exactly the property that makes a
  self-similarity comparison good at finding *repeats*. So segment types (A/B/C: which
  sections are the same material) are computed by feature distance between segments, and
  the drop is still the largest low-band step. Different question, different tool; §1 is
  not contradicted by using its own observation for the thing it argues is true.

None of the three has been checked against a second song, because there is one song. They
are triage that says how it decided, not authority.

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
from dataclasses import replace
from pathlib import Path

from remixkit.domain.models import SectionRole
from remixkit.ports.audio import AnalyzedSection, AudioAnalysis, BarFeature
from remixkit.services.errors import AnalysisUnavailable

log = logging.getLogger(__name__)

SR = 22050
HOP = 256
NFFT = 1024
LOW_HZ = 200.0  # the kick band, per measure_structure. Above this a filter sweep still
#                 has content and a breakdown stops being legible as a gap.

# The four bands a bar is described in. Split where instruments are, not on octaves: the
# kick's fundamental, the bass and low body above it, the range a voice and a lead occupy,
# and the hats. A fifth band would be finer without being more legible, and the point of
# this vector is that a person can read a row of it.
BANDS: list[tuple[str, float, float]] = [
    ("sub", 20.0, 120.0),
    ("low-mid", 120.0, 500.0),
    ("presence", 500.0, 4000.0),
    ("air", 4000.0, 20000.0),
]

# Where a lead vocal lives. `tonal_mid` is the tonal (non-noise) share of this band —
# high on a sung line or a lead synth, low on a bar that is only drums. It is a proxy and
# the port says so; nothing in this file calls it a vocal.
VOICE_LO, VOICE_HI = 300.0, 3400.0

# How much audio to look at. 10 minutes covers any single, and bounds the cost of a
# comb fit on a file somebody uploaded by mistake.
WINDOW_S = 600.0

# A step counts as structural when it is this many times the typical bar-to-bar movement
# in the same window. Scaled to the material rather than typed as a constant — the study's
# calibration is one positive example, which makes it triage, not authority.
CLIFF_FACTOR = 3.0

FULL_ENERGY = 0.85  # of the loudest section's kick energy → the same tier as the drop
MID_ENERGY = 0.40   # below this a section reads as a breakdown rather than a verse

# A flux peak counts as an onset at half a standard deviation above the track's mean. The
# envelope is z-scored, so this is a threshold in units of the material, not of the file.
ONSET_SD = 0.5

# Two segments are the same material under this fraction of the track's median pairwise
# segment distance. Scaled to the song, for the reason `_segment_types` gives.
SAME_MATERIAL = 0.6

# The least combined movement, in standard deviations of this track's own variation summed
# across the seven features, that counts as a texture boundary. An absolute floor under the
# median-scaled rule, because inside a homogeneous track the median is nearly zero and
# three times nearly zero cuts a steady chorus into pieces. See `_texture_edges`.
TEXTURE_FLOOR = 1.5

# The least a kick-band step must be, as a share of the track's 90th-percentile bar, to
# count as a section boundary. The same absolute floor under the same median-scaled rule,
# for the same reason. See `_sections`.
KICK_FLOOR_SHARE = 0.05


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
        # One STFT, everything from it. The onset envelope and the per-beat band measures
        # were each framing the signal separately, which is the same window function over
        # the same samples twice — and the bar features would have made it three times.
        mag = _spectrogram(np, samples)
        env = _onset_envelope(np, mag)
        fps = SR / HOP

        bpm, phase_s = _comb_fit(np, env, fps)
        beat_s = 60.0 / bpm
        beat_times = np.arange(phase_s % beat_s, len(samples) / SR, beat_s)
        beat_times = beat_times[beat_times * SR < len(samples) - NFFT]
        if len(beat_times) < 16:
            raise AnalysisUnavailable(
                "Too few beats to measure. This file is shorter than the analyser's minimum."
            )

        rms, low = _beat_measures(np, samples, mag, beat_times)

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

        # -- the bar vector, and everything read off it ------------------------
        bars = _bar_features(
            np, mag=mag, env=env, rms=rms, beat_times=beat_times, bar_start_b=bar_start_b
        )
        sections = _sections(
            np,
            beat_times=beat_times,
            rms=rms,
            low=low,
            bars=bars,
            bar_start_b=bar_start_b,
            drop_b=drop_b if drop_confident else None,
        )
        if not sections:
            warnings.append("No section boundaries found — the track's energy never steps.")

        method = _method_line(bpm, drop_confident, drop_ms is not None, len(beat_times))
        analysis = AudioAnalysis(
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
            bars=bars,
        )
        return replace(analysis, sheet=_song_sheet(analysis))


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
        f"sections at bar resolution, boundaries = <{LOW_HZ:.0f}Hz steps over "
        f"{CLIFF_FACTOR:.0f}× the median bar-to-bar step (floor {KICK_FLOOR_SHARE:.0%} of "
        f"the 90th-percentile bar) united with peaks in 7-feature novelty over "
        f"{TEXTURE_FLOOR:.1f} SD; segment types by feature distance under "
        f"{SAME_MATERIAL:.0%} of the median pairwise distance"
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
def _spectrogram(np, x):
    """Magnitude STFT — the one pass everything else reads from."""
    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, NFFT), strides=(x.strides[0] * HOP, x.strides[0])
    ) * win
    return np.abs(np.fft.rfft(frames, axis=1))


def _onset_envelope(np, mag):
    """Half-wave-rectified spectral flux, per frame. `measure_beat.onset_envelope`."""
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


def _beat_measures(np, x, mag, beat_times):
    """Loudness and kick energy, one value per beat. `measure_structure.beat_measures`.

    Beat-synchronous rather than fixed-window: the question is whether bar 6 differs from
    bar 7, and a window straddling the boundary blurs the edge being looked for.
    """
    fps = SR / HOP
    nlow = max(1, int(LOW_HZ / (SR / NFFT)))

    rms, low = [], []
    for a, b in zip(beat_times, beat_times[1:]):
        seg = x[int(a * SR) : int(b * SR)].astype(np.float64)
        rms.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9) if seg.size else -99.0)
        fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        low.append(float(mag[fa:fb, :nlow].mean()))
    return np.array(rms), np.array(low)


def _bar_features(np, *, mag, env, rms, beat_times, bar_start_b) -> list[BarFeature]:
    """Every bar as a vector. The curve the console draws and the segmenter cuts.

    Band energies come out on **one shared scale** — each divided by the 95th-percentile
    bar *total*, not by its own band's maximum. Per-band normalisation would make every
    band peak at 1.0 somewhere, which draws a track with no top end identically to one
    with plenty; a shared scale means the height of a stacked bar is the bar's energy and
    the split within it is the bar's balance. Both are then things a chart can be read for.

    Clipped at 1.5 rather than 1.0 because the percentile is not the maximum and a track's
    loudest bar is allowed to be off the top of the scale — flattening it to 1.0 would
    erase exactly the moment somebody is looking for.
    """
    nbars = (len(rms) - bar_start_b) // 4
    if nbars < 1:
        return []

    freqs = np.fft.rfftfreq(NFFT, 1.0 / SR)
    masks = [(freqs >= lo) & (freqs < hi) for _, lo, hi in BANDS]
    voice = (freqs >= VOICE_LO) & (freqs < VOICE_HI)
    fps = SR / HOP

    raw_bands: list[list[float]] = []
    centroids: list[float] = []
    onsets: list[float] = []
    tonal: list[float] = []
    loudness: list[float] = []
    starts: list[int] = []

    for i in range(nbars):
        b0 = bar_start_b + i * 4
        t0 = float(beat_times[b0])
        t1 = float(beat_times[min(b0 + 4, len(beat_times) - 1)])
        f0 = int(t0 * fps)
        f1 = max(f0 + 1, min(int(t1 * fps), len(mag)))
        block = mag[f0:f1]
        if block.size == 0:
            break
        raw_bands.append([float(block[:, m].mean()) if m.any() else 0.0 for m in masks])
        centroids.append(float((block * freqs).sum() / (float(block.sum()) + 1e-9)))
        onsets.append(_count_onsets(np, env[f0:f1]))
        tonal.append(_tonal_share(np, block, voice))
        window = rms[b0 : b0 + 4]
        loudness.append(float(window.mean()) if window.size else -99.0)
        starts.append(int(t0 * 1000))

    if not raw_bands:
        return []

    bands = np.array(raw_bands, dtype=np.float64)
    scale = float(np.percentile(bands.sum(axis=1), 95)) or 1e-9
    norm = np.clip(bands / scale, 0.0, 1.5)

    tonal_raw = np.array(tonal, dtype=np.float64)
    tonal_scale = float(np.percentile(tonal_raw, 95)) or 1e-9
    tonal_norm = np.clip(tonal_raw / tonal_scale, 0.0, 1.0)

    return [
        BarFeature(
            index=i,
            start_ms=starts[i],
            sub=round(float(norm[i][0]), 4),
            low_mid=round(float(norm[i][1]), 4),
            presence=round(float(norm[i][2]), 4),
            air=round(float(norm[i][3]), 4),
            centroid_hz=round(centroids[i], 1),
            onsets=onsets[i],
            rms_db=round(loudness[i], 2),
            tonal_mid=round(float(tonal_norm[i]), 4),
        )
        for i in range(len(raw_bands))
    ]


def _count_onsets(np, segment) -> float:
    """How many onsets land in this stretch of the envelope — how busy the bar is.

    A local maximum standing half a standard deviation above the track's mean flux. The
    envelope is already z-scored, so that threshold is in units of the material rather
    than of the file's gain, and a quiet track is not read as an empty one.
    """
    if segment.size < 3:
        return 0.0
    mid = segment[1:-1]
    peaks = (mid > segment[:-2]) & (mid >= segment[2:]) & (mid > ONSET_SD)
    return float(int(peaks.sum()))


def _tonal_share(np, block, voice) -> float:
    """The tonal share of the vocal band — `tonal_mid` before normalisation.

    Spectral flatness is the geometric mean over the arithmetic mean of the band: 1.0 for
    noise, falling toward 0 as the energy collects into discrete partials, which is what a
    sung note or a played lead is and what a hi-hat is not. Multiplied by how much of the
    bar's energy is in that band at all, so a bar with one quiet tonal wisp under a wall
    of drums does not read the same as a bar with a vocal on top of it.
    """
    band = block[:, voice]
    if band.size == 0:
        return 0.0
    mean = float(band.mean())
    flatness = float(np.exp(np.log(band + 1e-9).mean()) / (mean + 1e-9))
    return max(0.0, 1.0 - flatness) * (mean / (float(block.mean()) + 1e-9))


def _feature_matrix(np, bars: list[BarFeature]):
    """Bars as z-scored rows — the space boundaries and segment types are measured in.

    Z-scored per column because the seven features are in seven units (normalised energy,
    hertz, a count, a ratio) and an un-scaled distance would be a statement about the
    centroid's numeric size rather than about the music.
    """
    if not bars:
        return np.zeros((0, 7))
    raw = np.array(
        [[b.sub, b.low_mid, b.presence, b.air, b.centroid_hz, b.onsets, b.tonal_mid] for b in bars],
        dtype=np.float64,
    )
    sd = raw.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return (raw - raw.mean(axis=0)) / sd


def _texture_edges(np, matrix) -> list[int]:
    """Bars where the whole vector steps, not only the kick.

    The L1 distance between consecutive bars across all seven features — which is what
    sees a section that changes texture while the kick keeps going, and on four-on-the-
    floor that is most of them.

    Three conditions, and the second exists because the first is not enough on its own.
    `CLIFF_FACTOR × median` is the triage rule the rest of this file uses, and it fails
    here: inside a long homogeneous section the bar-to-bar movement is near zero, so the
    median over a track that is mostly homogeneous is near zero too, and three times
    nearly nothing is still nearly nothing. A steady chorus then gets chopped into pieces
    at boundaries whose own `entry` line reads "no large level change", which is the
    analyser reporting that it cut where nothing happened.

    So a boundary must **also** clear an absolute floor of `TEXTURE_FLOOR` — and that
    number is in interpretable units because the matrix is z-scored: it is standard
    deviations of this track's own variation, summed over the seven features. And it must
    be a local maximum, because a real change is one bar's event, not a plateau of five
    bars all marginally over a threshold.
    """
    if len(matrix) < 4:
        return []
    novelty = np.abs(np.diff(matrix, axis=0)).sum(axis=1)
    typical = float(np.median(novelty)) or 1e-9
    floor = max(CLIFF_FACTOR * typical, TEXTURE_FLOOR)

    edges: list[int] = []
    for i, value in enumerate(novelty):
        if value <= floor:
            continue
        if i > 0 and novelty[i - 1] > value:
            continue
        if i + 1 < len(novelty) and novelty[i + 1] > value:
            continue
        edges.append(i + 1)
    return edges


def _segment_types(np, matrix, spans: list[tuple[int, int]]) -> list[str]:
    """Which of these segments are the same material. Letters, in order of first use.

    RHYTHM-STUDY §1 rules out checkerboard novelty for finding a *drop*, on the grounds
    that the material either side of a breakdown is similar — which is the property being
    used here, for the opposite question. Two segments are the same type when the distance
    between their mean feature vectors is under `SAME_MATERIAL` times the median distance
    between all pairs in this track. Scaled to the song rather than a typed constant: a
    track of near-identical sections and a track of wildly different ones both have a
    median, and neither should be forced onto the other's idea of "similar".

    Greedy and in song order, so the first chorus is A and the second is compared to it —
    which also makes `repeat_of` meaningful, since a type's anchor is its first instance.
    """
    if not spans:
        return []
    centres = np.array([matrix[a:b].mean(axis=0) for a, b in spans])
    if len(centres) == 1:
        return ["A"]

    pairs = np.array(
        [
            float(np.linalg.norm(centres[i] - centres[j]))
            for i in range(len(centres))
            for j in range(i + 1, len(centres))
        ]
    )
    threshold = SAME_MATERIAL * (float(np.median(pairs)) or 1e-9)

    letters: list[str] = []
    anchors: list[int] = []
    for i in range(len(centres)):
        match = next(
            (
                t
                for t, anchor in enumerate(anchors)
                if float(np.linalg.norm(centres[i] - centres[anchor])) <= threshold
            ),
            None,
        )
        if match is None:
            anchors.append(i)
            match = len(anchors) - 1
        letters.append(chr(ord("A") + match) if match < 26 else "?")
    return letters


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


def _sections(np, *, beat_times, rms, low, bars, bar_start_b: int, drop_b: int | None):
    """Bar-resolution segmentation of the whole track, then a role and a type per segment.

    Bars are phase-locked to the bar line found above, so a segment boundary is a bar line
    rather than wherever a beat index happened to fall. Boundaries are the **union** of two
    tests: a step in the kick band larger than the material's own typical bar-to-bar
    movement (the rule `plateau_beats` uses, which the study validated), and a step in the
    whole feature vector (`_texture_edges`, which the study did not). Union rather than
    replacement — the module docstring says why.

    Roles remain an inference and are now emitted with the evidence they were inferred
    from: the loudest tier is chorus material (and the segment starting at the drop is the
    drop), the middle tier is verse material, the quiet tier is a breakdown unless it is
    the first or last segment. `segment_type` is the separate question of which segments
    are the *same material*, which is what lets a person pick between two instances of a
    chorus knowing they will get the same music. This is a starting point somebody edits,
    which is why every section is renameable in the console and carries its own numbers.
    """
    nbars = len(bars) if bars else (len(low) - bar_start_b) // 4
    if nbars < 3:
        return []
    bar_low = np.array(
        [float(low[bar_start_b + i * 4 : bar_start_b + i * 4 + 4].mean()) for i in range(nbars)]
    )
    bar_rms = np.array(
        [float(rms[bar_start_b + i * 4 : bar_start_b + i * 4 + 4].mean()) for i in range(nbars)]
    )

    # The study's rule, plus a floor it did not need. `CLIFF_FACTOR × median` alone assumes
    # the median bar-to-bar step is a meaningful quantity, and on heavily quantised
    # material — which is most of what this product is pointed at — it is nearly zero
    # inside a section, so three times it admits numerical noise as a boundary. The floor
    # is a share of the track's own loud-bar energy, so it scales with the master and sits
    # two orders of magnitude below a real section change (the study's own boundary steps
    # 58.8 → 41.6 against a 90th-percentile bar of ~57, which is 30% of it, not 5%).
    #
    # Not verified against the study's track: that master is not in every checkout, so the
    # test that would confirm it skips here. Stated as the triage it is.
    steps = np.abs(np.diff(bar_low))
    typical = float(np.median(steps)) or 1e-9
    reference = float(np.percentile(bar_low, 90)) or 1e-9
    kick_floor = max(CLIFF_FACTOR * typical, KICK_FLOOR_SHARE * reference)
    kick_edges = {i + 1 for i, s in enumerate(steps) if s > kick_floor}

    matrix = _feature_matrix(np, bars[:nbars])
    edges = sorted({0, nbars} | kick_edges | set(_texture_edges(np, matrix)))

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
    # The end of the track is not exempt from the floor. Appending it unconditionally is
    # how a boundary two bars from the end produced a one-bar final "section", which then
    # appeared in the console as something a kit could cut to — a loop of one bar that no
    # rule downstream would pass. The last edge is absorbed instead.
    if len(merged) > 1 and edges[-1] - merged[-1] < 2:
        merged.pop()
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

    types = _segment_types(np, matrix, spans) if len(matrix) else ["A"] * len(spans)
    repeats = {letter: types.count(letter) for letter in set(types)}

    def beat_ms(beat_index: int) -> int:
        idx = min(max(beat_index, 0), len(beat_times) - 1)
        return int(float(beat_times[idx]) * 1000)

    out: list[AnalyzedSection] = []
    counters: dict[SectionRole, int] = {}
    first_of_type: dict[str, str] = {}
    for i, ((a, b), energy) in enumerate(zip(spans, energies)):
        ratio = min(energy / peak, 1.0)
        letter = types[i] if i < len(types) else "A"
        window = bars[a:b] if bars else []
        evidence: list[str] = []

        if drop_bar is not None and a == drop_bar:
            role = SectionRole.DROP
            evidence.append("starts at the measured kick re-entry")
        elif ratio >= FULL_ENERGY:
            role = SectionRole.CHORUS
            evidence.append(f"top energy tier — {ratio:.0%} of the track's 90th-percentile bar")
        elif ratio >= MID_ENERGY:
            role = SectionRole.VERSE
            evidence.append(f"middle energy tier — {ratio:.0%} of the 90th-percentile bar")
        elif i == 0:
            role = SectionRole.INTRO
            evidence.append(f"quiet ({ratio:.0%}) and first in the track")
        elif i == len(spans) - 1:
            role = SectionRole.OUTRO
            evidence.append(f"quiet ({ratio:.0%}) and last in the track")
        else:
            role = SectionRole.BREAKDOWN
            evidence.append(f"quiet ({ratio:.0%}) between two louder sections")

        if repeats.get(letter, 1) > 1:
            evidence.append(f"type {letter} — the same material appears {repeats[letter]}× here")
        else:
            evidence.append(f"type {letter} — this material appears once")
        if window:
            evidence.append(_texture_line(window))

        counters[role] = counters.get(role, 0) + 1
        label = f"{role.value.title()} {counters[role]}"
        start = bar_start_b + a * 4
        end = bar_start_b + b * 4
        out.append(
            AnalyzedSection(
                role=role,
                start_ms=beat_ms(start),
                end_ms=beat_ms(end),
                label=label,
                beats=(b - a) * 4,
                energy_low_band=round(energy, 3),
                energy_rms_db=round(float(bar_rms[a:b].mean()), 2),
                bar_start=a,
                bar_end=b,
                band_mix=_band_mix(window),
                brightness_hz=round(_mean(window, "centroid_hz"), 1) if window else None,
                onset_density=round(_mean(window, "onsets"), 2) if window else None,
                tonal_mid=round(_mean(window, "tonal_mid"), 3) if window else None,
                segment_type=letter,
                repeat_of=first_of_type.get(letter),
                evidence=evidence,
                entry=_entry_line(bars, a),
            )
        )
        first_of_type.setdefault(letter, label)
    return [s for s in out if s.duration_ms > 0]


# ---------------------------------------------------------------- describing a segment
def _mean(window: list[BarFeature], attribute: str) -> float:
    values = [getattr(b, attribute) for b in window]
    return sum(values) / len(values) if values else 0.0


def _band_mix(window: list[BarFeature]) -> list[float]:
    """The four bands as proportions of this section's energy, summing to 1.

    A *different* normalisation from `BarFeature`'s, and deliberately so. The bar curve is
    on a shared scale because a chart of it should show how loud each bar is; a section's
    mix is a composition, and the question it answers — what is this section made of — has
    the same answer whether the section is loud or quiet.
    """
    if not window:
        return []
    totals = [
        _mean(window, "sub"),
        _mean(window, "low_mid"),
        _mean(window, "presence"),
        _mean(window, "air"),
    ]
    scale = sum(totals) or 1e-9
    return [round(value / scale, 4) for value in totals]


def _texture_line(window: list[BarFeature]) -> str:
    """One sentence naming what this section sounds like, from the vector alone.

    Every clause is a threshold on a measured number, and the thresholds are on the
    normalised scale — so "sparse" means this section is sparse *for this track*, which is
    the only comparison the normalisation supports.
    """
    mix = _band_mix(window)
    tonal = _mean(window, "tonal_mid")
    onsets = _mean(window, "onsets")
    centroid = _mean(window, "centroid_hz")

    bits: list[str] = []
    if mix and mix[0] + mix[1] > 0.72:
        bits.append("bottom-heavy")
    elif mix and mix[2] + mix[3] > 0.62:
        bits.append("top-heavy, little bottom")
    else:
        bits.append("full band")
    bits.append(f"tonal mid {tonal:.2f}")
    bits.append("sparse" if onsets < 6 else ("busy" if onsets > 14 else f"{onsets:.0f} onsets/bar"))
    bits.append(f"centroid {centroid / 1000:.1f} kHz")
    return " · ".join(bits)


def _entry_line(bars: list[BarFeature], bar_index: int) -> str:
    """What changed at this section's first bar — the reason the boundary is here.

    A boundary is an assertion, and this is the assertion in words. When no band moved
    much it says so rather than inventing a cause: the texture test cuts on the whole
    vector, so a boundary with a flat level is a real boundary in the arrangement and a
    sentence claiming the kick entered would be false.
    """
    if bar_index <= 0 or bar_index >= len(bars):
        return "the track starts"
    previous, now = bars[bar_index - 1], bars[bar_index]
    moves = [
        ("sub", now.sub - previous.sub, "kick"),
        ("low-mid", now.low_mid - previous.low_mid, "bass"),
        ("presence", now.presence - previous.presence, "presence"),
        ("air", now.air - previous.air, "top end"),
    ]
    band, delta, what = max(moves, key=lambda move: abs(move[1]))
    if abs(delta) >= 0.05:
        return f"{what} {'enters' if delta > 0 else 'drops out'} ({delta:+.2f} {band})"

    brightness = now.centroid_hz - previous.centroid_hz
    if abs(brightness) > 250:
        return f"{'brighter' if brightness > 0 else 'darker'} by {abs(brightness):.0f} Hz"
    if abs(now.onsets - previous.onsets) >= 4:
        return f"{'busier' if now.onsets > previous.onsets else 'sparser'} by {abs(now.onsets - previous.onsets):.0f} onsets"
    return "no large level change — the boundary is in the arrangement, not the level"


# ---------------------------------------------------------------- the sheet
def _clock(ms: int | float) -> str:
    total = max(0.0, float(ms) / 1000.0)
    return f"{int(total // 60)}:{total % 60:04.1f}"


def _song_sheet(analysis: AudioAnalysis) -> str:
    """The arrangement, written out. What an agent is given instead of a float.

    Assembled by formatting the measurements above and nothing else. That is the property
    worth defending: every line here can be traced to a number this adapter produced, so
    putting the sheet in a prompt cannot introduce a claim that the analyser did not make.
    A model summarising the same numbers would read better and would be one paraphrase
    away from asserting something about the song that is not true — and the false sentence
    would look exactly like the true ones.

    Bars are numbered from 1 at the measured downbeat, because that is how a person counts
    them out loud. `BarFeature.index` stays 0-based; the conversion happens here only.
    """
    lines: list[str] = []

    head = []
    if analysis.bpm:
        head.append(f"{analysis.bpm:.2f} BPM")
    head.append("4/4 assumed")
    head.append(_clock(analysis.duration_ms))
    if analysis.bars:
        head.append(f"{len(analysis.bars)} bars measured")
    lines.append(" · ".join(head))

    if analysis.drop_ms is not None:
        bar = next(
            (b.index + 1 for b in reversed(analysis.bars) if b.start_ms <= analysis.drop_ms), None
        )
        where = f"bar {bar}" if bar else "an unnumbered bar"
        confirmed = (
            "confirmed as a bar line" if analysis.downbeat_ms is not None else "not confirmed as a bar line"
        )
        if analysis.bar_phase_confidence is not None:
            confirmed += f" ({analysis.bar_phase_confidence:.0%} of arrangement changes agree)"
        lines.append(f"The drop is at {where}, {_clock(analysis.drop_ms)} — {confirmed}.")
        tail = []
        if analysis.riser_beats:
            tail.append(f"a {analysis.riser_beats}-beat riser leads into it")
        if analysis.plateau_beats:
            tail.append(
                f"it holds full energy for {analysis.plateau_beats} beats "
                f"({analysis.plateau_beats // 4} bars)"
            )
        if tail:
            lines.append(" and ".join(tail).capitalize() + ".")
    else:
        lines.append("No drop was found — nothing in this track steps far enough in the low band.")

    if analysis.sections:
        lines.append("")
        lines.append("ARRANGEMENT — bar numbers count from the measured downbeat")
        for section in analysis.sections:
            span = (
                f"{section.bar_start + 1}–{section.bar_end}"
                if section.bar_start is not None and section.bar_end is not None
                else "?"
            )
            texture = next((e for e in section.evidence if " · " in e), "")
            lines.append(
                f"  bars {span:>9}  {section.segment_type or '?'}  "
                f"{section.role.value:<10} {_clock(section.start_ms):>7}  "
                f"{section.duration_ms / 1000:5.1f}s  {texture}"
            )
            reason = "; ".join(e for e in section.evidence if " · " not in e)
            detail = f"named from: {reason}" if reason else ""
            if section.repeat_of:
                detail += f" — same material as {section.repeat_of}"
            lines.append(f"{'':>17}   ↳ enters: {section.entry}. {detail}")

        loopable = [s for s in analysis.sections if s.role.is_hook_material]
        lines.append("")
        if loopable:
            lines.append("A KIT MAY CUT TO — sections tagged hook, chorus or drop")
            for section in loopable:
                lines.append(
                    f"  {section.label:<12} {_clock(section.start_ms)}–{_clock(section.end_ms)}  "
                    f"{section.beats or 0} beats  "
                    f"tonal mid {section.tonal_mid if section.tonal_mid is not None else 0:.2f}"
                )
        else:
            lines.append("A KIT MAY CUT TO — nothing. No section reached the top energy tier.")

    lines.append("")
    lines.append(f"MEASURED BY  {analysis.method}")
    if analysis.warnings:
        lines.append("")
        lines.append("NOT ESTABLISHED")
        for warning in analysis.warnings:
            lines.append(f"  - {warning}")
    return "\n".join(lines)


def master_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
