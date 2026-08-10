"""Measure a recording from a thirty-second preview, and say what it sounds like.

`SCOPE-RESET §2` lists musical measurement — BPM, energy, sections — as the first thing
derived from a track and the thing every downstream process queries rather than
recomputes. This is that measurement for the platform, and it exists because of a
concrete failure: **discovery for a new artist is cold-start blocked.** Deezer's related
artists are collaborative filtering over listening data, so an artist with no audience has
no neighbours, and `sources.deezer.find_counterparties` falls back to searching playlists
for the artist's *name* — which is what filled this cluster with curators called Amanda.
Style terms are the only input that still works, and the only two on the roster came from
Deezer's top-level genre labels. Deezer's own `bpm` field reads `0` for both of the
tracks this was written for.

So the record has to be listened to.

**Provenance, which is the whole discipline here.** `SCOPE-RESET §2a` rule 1 splits
`measured` — computed from the artefact itself — from `inferred`, a model's estimate. The
split runs straight through this module:

  * `bpm`, `onset_density` and `brightness_hz` are **measured**. They are numbers taken
    off the waveform.
  * every style term is **inferred**. A tempo band is evidence for a genre, never a
    statement of one, and 128 BPM is house, techno, trance and half of pop music.

The earlier audit's CRITICAL finding was code that "invents the most confident value when
the source is silent", and labelling a genre guess `measured` because a real FFT was
involved would be that bug in its purest form.

**What a preview is not.** Thirty seconds, usually lifted from the middle of the record.
Tempo survives that fine — it is the most robust thing in the signal. An energy *curve*,
a section map or a drop location do not: they are properties of an arrangement, and most
of the arrangement is not in the file. This module measures only what a clip can support.

Requires `ffmpeg` on PATH and numpy, and therefore **does not run in the web Lambda**,
whose bundle is deliberately small (`requirements.txt` has no numpy, and `embed.py` uses
`urllib` for the same reason). numpy is imported inside the functions that need it so
that importing `rtf_platform` costs the Lambda nothing. The agent that calls this is
drained by a worker:

    python -m rtf_platform.ingest --drain-only --kinds analyse_recording

which is the fleet's design working as advertised: a heavyweight agent joins by claiming
leases, and no other tier learns it exists.
"""

from __future__ import annotations

import subprocess
from typing import Any

#: Decode target. 22.05 kHz mono is what `content/bin/measure_beat.py` uses and is ample
#: for onset detection — the transients that carry tempo live far below Nyquist here.
SR = 22050
HOP = 256
NFFT = 1024

#: The tempo search window. Everything is folded into it by octave (see `_fold`), because
#: a comb at 75 BPM scores identically to one at 150 and the fold is what makes the answer
#: single-valued. 70–180 covers every dance idiom this label works in.
BPM_LO, BPM_HI = 70.0, 180.0

#: Tempo bands to candidate style terms, for the *search vocabulary* only.
#:
#: Read this as "playlists worth searching", never as "this record is trance". Bands
#: overlap deliberately: a 128 BPM record should search house *and* indie dance, because
#: the point is to widen a two-term query, not to win an argument about taxonomy. Every
#: term produced here is written `inferred` and carries the band that produced it.
_BANDS: tuple[tuple[float, float, tuple[str, ...]], ...] = (
    (70.0, 90.0, ("downtempo", "trip hop", "chillout")),
    (88.0, 108.0, ("hip hop", "trap", "future beats")),
    (108.0, 122.0, ("indie dance", "nu disco", "alt dance")),
    (120.0, 132.0, ("house", "melodic house", "progressive house")),
    (126.0, 136.0, ("techno", "melodic techno", "electro")),
    (134.0, 146.0, ("trance", "progressive trance", "hard dance")),
    (144.0, 162.0, ("dubstep", "future garage", "uk garage")),
    (160.0, 180.0, ("drum and bass", "jungle", "breakbeat")),
)


class Unmeasurable(RuntimeError):
    """The audio could not be decoded or was too short to measure.

    Raised rather than returning a zero, because a BPM of 0 written as `measured` is
    exactly the invented-confident-value failure this module's docstring is about.
    """


def decode(data: bytes) -> Any:
    """Preview bytes to a mono float32 array, via ffmpeg on stdin.

    stdin rather than a temporary file so nothing has to be cleaned up on a worker that
    may be killed mid-lease — the fleet's whole fault model is that a worker can vanish.
    """
    import numpy as np

    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR),
             "-f", "f32le", "pipe:1"],
            input=data, capture_output=True, check=True, timeout=60)
    except FileNotFoundError as exc:
        raise Unmeasurable("ffmpeg is not on PATH — this agent needs a worker, "
                           "not the web Lambda") from exc
    except subprocess.CalledProcessError as exc:
        raise Unmeasurable(f"ffmpeg refused the audio: "
                           f"{exc.stderr.decode('utf-8', 'replace')[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unmeasurable("ffmpeg did not finish within 60s") from exc

    samples = np.frombuffer(done.stdout, dtype=np.float32)
    if len(samples) < SR * 5:
        raise Unmeasurable(
            f"{len(samples) / SR:.1f}s of audio is not enough to measure a tempo")
    return samples


def decode_file(path: str) -> Any:
    """The same conversion, for a file on disk rather than bytes in memory.

    `decode` pipes through stdin and says why: nothing to clean up on a worker that may
    be killed mid-lease. That reasoning holds for a 30-second preview and inverts for a
    master. A preview is ~500 KB; a 24-bit/96 kHz master is 200 MB, and `subprocess.run`
    with `input=` holds the whole thing in the parent process while ffmpeg holds its own
    copy in the pipe. The temporary file is the *smaller* footprint, and the caller
    already has one — it streamed the download to disk so it could hash the bytes on the
    way past.

    ffmpeg is given the path directly, so it seeks rather than buffers, and a corrupt
    header fails immediately instead of after a 200 MB transfer into a pipe.
    """
    import numpy as np

    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
             "-f", "f32le", "pipe:1"],
            capture_output=True, check=True, timeout=300)
    except FileNotFoundError as exc:
        raise Unmeasurable("ffmpeg is not on PATH — this agent needs a worker, "
                           "not the web Lambda") from exc
    except subprocess.CalledProcessError as exc:
        raise Unmeasurable(f"ffmpeg refused the audio: "
                           f"{exc.stderr.decode('utf-8', 'replace')[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        # Five minutes rather than `decode`'s sixty seconds. A master is two orders of
        # magnitude larger than a preview and decoding is linear in its length.
        raise Unmeasurable("ffmpeg did not finish within 300s") from exc

    samples = np.frombuffer(done.stdout, dtype=np.float32)
    if len(samples) < SR * 5:
        raise Unmeasurable(
            f"{len(samples) / SR:.1f}s of audio is not enough to measure a tempo")
    return samples


def probe(path: str) -> dict[str, int]:
    """The file's own sample rate, channel count and duration, from ffprobe.

    Deliberately **not** derived from what `decode_file` returns. That array is mono at
    22050 Hz because this module asked for it — reporting those numbers as the asset's
    shape would record our own resampling settings as a measurement of the master, which
    is a fact about this code masquerading as a fact about the record.

    ffprobe ships with ffmpeg, so this adds no dependency the module did not already
    have. Raises rather than returning partial values: a caller writing `sample_rate`
    into `recording_asset` needs all three or none, and NULL already means "nothing has
    opened this file".
    """
    import json

    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels:format=duration",
             "-of", "json", path],
            capture_output=True, check=True, timeout=60)
    except FileNotFoundError as exc:
        raise Unmeasurable("ffprobe is not on PATH — it ships with ffmpeg, so this is "
                           "a partial install rather than a missing package") from exc
    except subprocess.CalledProcessError as exc:
        raise Unmeasurable(f"ffprobe refused the audio: "
                           f"{exc.stderr.decode('utf-8', 'replace')[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unmeasurable("ffprobe did not finish within 60s") from exc

    try:
        parsed = json.loads(done.stdout)
        stream = parsed["streams"][0]
        return {
            "sample_rate": int(stream["sample_rate"]),
            "channels": int(stream["channels"]),
            "duration_ms": int(float(parsed["format"]["duration"]) * 1000),
        }
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise Unmeasurable(
            "ffprobe found no audio stream to describe in that file") from exc


def onset_envelope(x: Any) -> Any:
    """Half-wave-rectified spectral flux, per frame.

    The same estimator as `content/bin/measure_beat.py`, deliberately: two tempo numbers
    in one repository that disagree because they were computed differently is worse than
    either of them being slightly off.
    """
    import numpy as np

    n = 1 + (len(x) - NFFT) // HOP
    if n < 16:
        raise Unmeasurable("too few frames to estimate a tempo")
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, NFFT), strides=(x.strides[0] * HOP, x.strides[0])) * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(0, np.diff(np.log1p(mag * 10), axis=0)).sum(axis=1)
    flux -= flux.mean()
    return flux / (flux.std() + 1e-9)


def _fold(bpm: float) -> float:
    """Fold a tempo into `BPM_LO..BPM_HI` by doubling or halving.

    A beat comb cannot distinguish a tempo from its octaves — every second beat of a 150
    BPM grid is a 75 BPM grid, and both fit the onsets exactly. Folding does not *solve*
    that ambiguity, it makes the answer deterministic, which is what a search vocabulary
    needs. `measure` reports the ambiguity separately rather than pretending it is gone.
    """
    while bpm < BPM_LO:
        bpm *= 2
    while bpm >= BPM_HI:
        bpm /= 2
    return bpm


#: Ratios a beat comb genuinely cannot tell apart, as (multiplier, name).
#:
#: The octave pair is obvious — every second beat of a 150 grid is a 75 grid. The 3:2 pair
#: is the one that actually bites, and `content/bin/measure_beat.py` documents it in the
#: same words: *"a full-track tempo sweep can't tell 125 BPM from its dotted-quarter alias
#: at 83.33 — both combs score the same."* It is not hypothetical here. The first record
#: this module was run against, an electro single, measured 84.0 and was handed
#: "downtempo, trip hop, chillout".
_ALIASES: tuple[tuple[float, str], ...] = (
    (2.0, "double-time"), (0.5, "half-time"),
    (1.5, "dotted-quarter"), (2 / 3, "two-thirds"),
    (3.0, "triple-time"), (1 / 3, "third-time"),
)

#: How close a rival's comb score must be to the winner's, as a fraction, before the
#: tempo is called ambiguous. Combs at true aliases score within noise of each other, so
#: this is generous on purpose: the cost of abstaining is two fewer search terms, and the
#: cost of guessing wrong is a shortlist of trip-hop curators for a dance record.
_ALIAS_MARGIN = 0.08


def tempo(env: Any) -> tuple[float, float, list[float]]:
    """`(bpm, confidence, rivals)` by scoring a beat comb across the tempo range.

    Confidence is the winner's separation from the field in standard deviations, squashed
    into 0–1 — *not*, as this first read it, its ratio to the mean, which saturated at 1.0
    for every input and made the floor in `style_terms` unreachable. A gate that never
    fires is worse than no gate, because it reads as one.

    `rivals` are tempi that scored within `_ALIAS_MARGIN` of the winner **and** stand in
    one of the `_ALIASES` ratios to it. A non-empty list means the comb cannot distinguish
    this record's pulse from a harmonic relative of it, which is a fact about the method,
    not about the record, and the caller is told rather than handed a number that hides it.
    """
    import numpy as np

    fps = SR / HOP
    n = len(env)
    scores: list[tuple[float, float]] = []
    for bpm in np.arange(BPM_LO, BPM_HI, 0.25):
        period = 60.0 / bpm * fps
        nbeats = int((n - 1) / period)
        if nbeats < 8:
            continue
        k = np.arange(nbeats)
        phases = np.arange(0, period, 0.5)
        idx = np.rint(phases[:, None] + k[None, :] * period).astype(int)
        score = env[np.clip(idx, 0, n - 1)].sum(axis=1) / nbeats
        scores.append((float(score.max()), float(bpm)))

    if not scores:
        raise Unmeasurable("the clip is too short for eight beats at any tempo")

    best, bpm = max(scores)

    # Prefer the faster reading of an octave tie. A comb at half tempo lands on every
    # *other* transient and therefore scores as well as the true grid almost by
    # construction — measured: a clean 140 BPM click train scores 5.297 at 70 and 5.282
    # at 140, a difference of 0.3%. Where the two are that close the faster one explains
    # every onset and the slower one explains half of them, so the faster one is the
    # pulse. This is a tie-break, not alias resolution: it only fires on near-equality,
    # and anything short of that is left to the rival check below.
    by_bpm = {round(b, 2): s for s, b in scores}
    for _ in range(2):
        doubled = by_bpm.get(round(bpm * 2, 2))
        if doubled is not None and doubled >= best * 0.95 and bpm * 2 < BPM_HI:
            bpm, best = bpm * 2, doubled
        else:
            break

    # Separation from the field, as a ratio to the mean comb score rather than to its
    # standard deviation. The z-score reading this first used does not discriminate: the
    # maximum of several hundred candidates sits three-plus standard deviations above
    # their mean for *any* input, so white noise scored a confidence of 1.0 and the floor
    # in `style_terms` never engaged. Measured on the ratio: noise 1.80, a click train
    # 16.7–19.0. The divisor below puts the boundary between them.
    values = np.array([s for s, _ in scores])
    mean = float(values.mean())
    confidence = 0.0 if mean <= 0 else max(0.0, min(1.0, (best / mean - 1.0) / 8.0))

    rivals: list[float] = []
    for score, candidate in scores:
        if candidate == bpm or score < best * (1 - _ALIAS_MARGIN):
            continue
        for ratio, _ in _ALIASES:
            # 1.5% tolerance: the sweep steps in 0.25 BPM, so an exact ratio rarely lands
            # exactly on a sampled candidate.
            if abs(candidate - bpm * ratio) <= max(1.0, bpm * ratio * 0.015):
                folded = _fold(candidate)
                if all(abs(folded - r) > 1.0 for r in rivals):
                    rivals.append(folded)
                break

    return _fold(bpm), confidence, [r for r in rivals if abs(r - _fold(bpm)) > 1.0]


def brightness(x: Any) -> float:
    """Spectral centroid in Hz — the balance point of the spectrum.

    A coarse timbre axis, and the only one a thirty-second clip supports honestly. It
    separates a dark, bass-led record from a bright, hi-hat-led one, which is a real
    distinction between adjacent dance idioms at the same tempo. It is *not* a genre.
    """
    import numpy as np

    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, NFFT), strides=(x.strides[0] * HOP, x.strides[0])) * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(NFFT, 1.0 / SR)
    total = mag.sum(axis=1)
    live = total > 1e-6
    if not live.any():
        raise Unmeasurable("the clip is silent")
    return float(((mag[live] * freqs).sum(axis=1) / total[live]).mean())


def style_terms(bpm: float, confidence: float, rivals: list[float] | None = None,
                *, limit: int = 6) -> list[str]:
    """Candidate search terms for this tempo. **Inferred, never measured.**

    Abstains in two cases, and abstaining is the point of the function rather than an
    edge in it. `SCOPE-RESET §5` keeps "mark what is not verified rather than guessing
    it" as one of three things that survive every scope change, and cites
    `screen_clips.py` abstaining rather than judging a locked-off shot as the reference.
    This is the same call:

      * **low confidence** — no tempo could be established, so there is nothing to map;
      * **a harmonic rival** — a tempo *was* established and the method cannot tell it
        from 1.5× or half of itself. The two land in different bands with disjoint
        vocabularies, so emitting the winner's band is a coin flip dressed as a
        measurement. Returning both bands would be worse still: six terms of which three
        are wrong widen the query and poison it at the same time.

    A shortlist widened by terms drawn from a tempo nobody could establish is worse than
    a narrow one, because the noise gets embedded and ranked and an operator cannot see
    where it came from — the mechanism by which "Amanda" became a curator shortlist.
    """
    if confidence < 0.15 or rivals:
        return []
    out: list[str] = []
    for lo, hi, terms in _BANDS:
        if lo <= bpm < hi:
            out += [t for t in terms if t not in out]
    return out[:limit]


def measure(data: bytes) -> dict[str, Any]:
    """Everything this module can say about a preview clip, with its provenance attached.

    The return is deliberately shaped as facts-with-labels rather than a bag of numbers,
    so a caller cannot write a style term as `measured` by accident. `harvested.py` exists
    because callers did exactly that with `.get("provenance", "measured")`.
    """
    return _facts(decode(data), basis="deezer_preview_30s")


def measure_file(path: str, *, basis: str) -> dict[str, Any]:
    """The same measurements, taken from a whole file, saying what file it was.

    `basis` has no default and that is the point. The one thing this module must never
    do is let a measurement forget what it listened to: 125 BPM off a master and 125 BPM
    off a thirty-second excerpt are the same number and different claims, and only the
    first can be extended with the section map, energy curve and hook window that
    `SCOPE-RESET §2` asks for. `analyse_recording` passes the asset id, so the fact it
    writes can point at the exact object through `fact_basis`.
    """
    return _facts(decode_file(path), basis=basis)


def _facts(samples: Any, *, basis: str) -> dict[str, Any]:
    """Shared by both entry points. The provenance split lives here, and
    `tests/test_audio.py::ProvenanceShape` reads this function's source to check that a
    style term can never be reached from the `measured` key."""
    env = onset_envelope(samples)
    bpm, confidence, rivals = tempo(env)
    centroid = brightness(samples)

    return {
        "measured": {
            "bpm": round(bpm, 1),
            "brightness_hz": round(centroid, 1),
            "duration_s": round(len(samples) / SR, 1),
        },
        "inferred": {
            "bpm_confidence": round(confidence, 3),
            # Reported even when empty, and *especially* then: a caller looking at no
            # style terms needs to know whether the record was unmeasurable or merely
            # ambiguous, because only the second is worth asking a human to resolve.
            "tempo_rivals": [round(r, 1) for r in rivals],
            "style_terms": style_terms(bpm, confidence, rivals),
        },
        # Named so a fact written from this can say what it listened to. A measurement
        # taken from a thirty-second preview is a different claim from one taken off the
        # master, and a row that cannot tell them apart will eventually be asked to.
        "basis": basis,
    }
