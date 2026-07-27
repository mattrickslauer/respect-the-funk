#!/usr/bin/env python3
"""Measure a song's own structure, so the edit can be derived from it instead of guessed.

`measure_beat.py` answers *where are the beats*. This answers the questions FORMAT-SPEC
currently settles by a person listening:

    §Tuning step 3   "Listen from the drop forward and find where the arrangement starts
                      breathing — cutting past that point fights the music."
                                                                        → carousel_beats
    §Any cast        every picture's `intensity` 1–5, hand-assigned     → an energy curve
    MEME-ENGINE      "a target that climbs, peaks at ~85% ..."          — typed into the
                                                                          solver, not
                                                                          taken off track

All of them are functions of the audio, and this computes them off three per-beat
measures taken straight from the waveform:

    rms_db    loudness. 20·log10 of the RMS over the beat.
    low       mean magnitude below 200Hz. The kick. This is the one that finds a drop:
              a drop is a kick re-entering, and nothing else in a mix moves this far.
    flux      rectified spectral flux summed over the beat. Event density.

TWO METHODS THAT DO NOT WORK ON THIS MATERIAL, both tried and both discarded here so the
next person does not spend the afternoon:

  1. FOOTE CHECKERBOARD NOVELTY does not find a drop. A drop is not a section boundary in
     the Foote sense — it is a re-entry, and the full-energy material 4 bars before the
     breakdown closely resembles the full-energy material after it, which is precisely
     the configuration the checkerboard kernel scores LOW. On `losing-sleep` the measured
     drop ranks 8th–79th by novelty depending on the feature set, never 1st. Worse, the
     usual cosine self-similarity matrix L2-normalises every feature row, which removes
     loudness — the one dimension a drop is actually made of.

  2. BEAT-ACCENT COMBS do not find the bar line. Scoring a bar-period comb at each of the
     four phases is standard and it is what `measure_beat.downbeat()` does. On four-on-
     the-floor it cannot work: every beat carries a kick, so the four phases came out
     within 5–9% of each other AND disagreed between measures (low-band picked phase 2,
     flux picked 1 and 3). That is not a weak signal, it is no signal.

WHAT DOES WORK for bar phase is the arrangement. A mix changes on bar lines and almost
nowhere else, so the structural events ARE the phase reference: find the boundaries, and
the phase is the one that makes them integer bars apart. See `bar_phase_from_structure`.

    .venv/bin/python bin/measure_structure.py losing-sleep
    .venv/bin/python bin/measure_structure.py losing-sleep --against california-test

Requires: ffmpeg on PATH, numpy. Shares its decoder with measure_beat.py.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import measure_beat as mb  # noqa: E402
import rtf  # noqa: E402

LOW_HZ = 200.0      # kick band. Above this a filter sweep still has content and the
                    # breakdown stops being legible as a gap.
PLATEAU_DB = 1.5    # a bar is still "in the plateau" while within this of the drop bar.
                    # Set from the measured spread of losing-sleep's own drop section,
                    # which holds inside 1.5dB for seven bars and then falls 1.1dB in one.


# ---------------------------------------------------------------- measures


def beat_measures(x: np.ndarray, beat_times: np.ndarray) -> dict:
    """Loudness, kick energy and event density, one value per beat.

    Beat-synchronous rather than fixed-window: the whole question is whether bar 6 differs
    from bar 7, and a window straddling the boundary blurs the edge being looked for.
    """
    n = 1 + (len(x) - mb.NFFT) // mb.HOP
    win = np.hanning(mb.NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, mb.NFFT), strides=(x.strides[0] * mb.HOP, x.strides[0])) * win
    mag = np.abs(np.fft.rfft(frames, axis=1))
    logmag = np.log1p(mag * 10)
    flux = np.maximum(0, np.diff(logmag, axis=0)).sum(axis=1)
    fps = mb.SR / mb.HOP
    nlow = max(1, int(LOW_HZ / (mb.SR / mb.NFFT)))

    rms, low, fl = [], [], []
    for a, b in zip(beat_times, beat_times[1:]):
        sa, sb = int(a * mb.SR), int(b * mb.SR)
        seg = x[sa:sb].astype(np.float64)
        rms.append(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9) if seg.size else -99.0)
        fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        low.append(float(mag[fa:fb, :nlow].mean()))
        fl.append(float(flux[fa:min(fb, len(flux))].sum()))
    return {"rms": np.array(rms), "low": np.array(low), "flux": np.array(fl)}


def find_drop(low: np.ndarray, near_b: int, radius: int = 24) -> int:
    """The beat where the kick comes back. Largest positive step in low-band energy.

    A drop is the only place in a mix where this measure moves by an order of magnitude:
    on `losing-sleep` it goes 0.51 → 53.45 across one beat, because the bar before it is
    a filter sweep with the bottom removed. Searching near a stated estimate rather than
    globally, for the same reason measure_beat snaps rather than scans — a track has more
    than one drop and only a person knows which one the video is about.
    """
    lo, hi = max(1, near_b - radius), min(len(low), near_b + radius)
    step = [(low[i] - low[i - 1], i) for i in range(lo, hi)]
    return max(step)[1] if step else near_b


def section_edges(rms: np.ndarray, low: np.ndarray, drop_b: int, bars: int = 16) -> list:
    """Bar-resolution arrangement changes around the drop, as (bar_rel, rms, low).

    Averaged per bar before differencing. A per-beat difference finds every fill; the
    question here is which BAR the arrangement changed on.
    """
    out = []
    for b in range(-bars, bars + 1):
        idx = [drop_b + b * 4 + k for k in range(4) if 0 <= drop_b + b * 4 + k < len(rms)]
        if len(idx) == 4:
            out.append((b, float(rms[idx].mean()), float(low[idx].mean())))
    return out


def change_points(rms: np.ndarray, low: np.ndarray, drop_b: int,
                  span: int = 64, k: int = 6, min_sep: int = 4) -> list:
    """The k biggest arrangement changes near the drop, at BEAT resolution.

    Deliberately not bar-aligned. Bar-averaging first and then looking for bar multiples
    is circular — it can only ever find what it assumed. These are found beat by beat and
    their spacing is then examined, which is a test the data can fail.

    The step measure adds a loudness term and a kick term, each scaled by its own rough
    spread so neither dominates: a filter sweep moves the kick and barely moves the RMS,
    a breakdown moves both.
    """
    out = []
    lo, hi = max(1, drop_b - span), min(len(rms), drop_b + span)
    steps = sorted(((abs(rms[i] - rms[i - 1]) / 3.0 + abs(low[i] - low[i - 1]) / 10.0, i)
                    for i in range(lo, hi)), reverse=True)
    for d, i in steps:
        if any(abs(i - j) < min_sep for _, j in out):
            continue
        out.append((d, i))
        if len(out) >= k:
            break
    return sorted(((i - drop_b), d) for d, i in out)


def bar_phase_from_structure(cps: list) -> tuple:
    """Recover the bar line from where the arrangement changes, not from beat accents.

    The inference, stated so it can be argued with: a mix changes its arrangement on bar
    lines and almost nowhere else. So if every large change near the drop sits at the same
    residue mod 4 relative to the drop, that residue is the bar phase — and if the residue
    is 0, the drop is itself a bar line.

    This can fail, and the failure is visible: changes scattered across all four residues
    mean the material has no bar-level structure this method can see, and the caller
    should not pretend otherwise.
    """
    res = [int(rel) % 4 for rel, _ in cps]
    counts = [res.count(p) for p in range(4)]
    best = int(np.argmax(counts))
    return (-best) % 4, counts


def plateau_beats(low: np.ndarray, drop_b: int, max_bars: int = 16) -> int:
    """How many beats the drop holds full energy, in whole bars.

    FORMAT-SPEC's "where the arrangement starts breathing", as a number. Measured on the
    KICK, not on loudness: a mastered drop is limited to within a couple of dB, so RMS
    barely moves when the section ends, while the low band falls off a cliff. On
    `losing-sleep` the bar-to-bar loudness step at the section end is 1.09dB — inside the
    noise of every other bar — and the low-band step is 58.8 → 41.6.
    """
    bars = []
    for b in range(max_bars):
        idx = slice(drop_b + b * 4, drop_b + b * 4 + 4)
        if len(low[idx]) < 4:
            break
        bars.append(float(low[idx].mean()))
    if len(bars) < 3:
        return len(bars) * 4
    steps = [bars[b] - bars[b + 1] for b in range(len(bars) - 1)]
    # The FIRST cliff, not the biggest one: a track has several sections and only the
    # nearest boundary bounds this carousel. "Cliff" is scaled to the material rather
    # than typed as a constant — 3× the typical bar-to-bar movement in this same window.
    typical = float(np.median(np.abs(steps)))
    for b, s in enumerate(steps):
        if s > 3 * typical:
            return (b + 1) * 4
    return len(bars) * 4


def quantize_intensity(e: np.ndarray) -> list:
    """Map an energy curve onto the format's 1–5 scale, standardised on its own spread."""
    lo, hi = float(e.min()), float(e.max())
    if hi - lo < 1e-9:
        return [3] * len(e)
    return [int(np.clip(round(1 + 4 * v), 1, 5)) for v in (e - lo) / (hi - lo)]


def pearson(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std()))


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("song")
    ap.add_argument("--window", type=float, default=600.0)
    ap.add_argument("--against", action="append", default=[])
    a = ap.parse_args()

    root = rtf.find_root(Path.cwd())
    spath = root / "lib" / "songs" / f"{a.song}.song.yaml"
    song = rtf.load(spath)
    meas = song["measured"]
    bpm = float(meas["bpm"])
    beat_s = 60.0 / bpm
    stated_drop_s = float(meas["drop_ms"]) / 1000.0

    x = mb.decode(str((spath.parent / song["file"]).resolve()), 0.0, a.window)
    # Grid phase-locked to the stated drop: the drop is the origin of every grid in this
    # project, so it is the only sane place to hang the beat grid off.
    t0 = stated_drop_s % beat_s
    bt = np.arange(t0, a.window, beat_s)
    bt = bt[bt * mb.SR < len(x) - mb.NFFT]
    M = beat_measures(x, bt)
    rms, low = M["rms"], M["low"]
    n = len(rms)
    drop_b = int(round((stated_drop_s - bt[0]) / beat_s))

    print(f"{'=' * 78}\n{song['title']} — {song['artist']}   {bpm:.2f} BPM   "
          f"beat {beat_s * 1000:.0f}ms   bar {beat_s * 4000:.0f}ms")
    print(f"{n} beats analysed ({bt[-1]:.1f}s of audio)\n")

    # ---- the drop -------------------------------------------------------------------
    found = find_drop(low, drop_b)
    print("THE DROP")
    print(f"  declared measured.drop_ms   {meas['drop_ms']:>8} ms  (beat {drop_b})")
    print(f"  largest kick re-entry       {bt[found] * 1000:>8.0f} ms  (beat {found}, "
          f"{(found - drop_b) * beat_s * 1000:+.0f} ms)")
    print(f"  low-band across that step   {low[found - 1]:.2f} → {low[found]:.2f}   "
          f"({low[found] / max(low[found - 1], 1e-6):.0f}×)")
    print(f"  loudness across that step   {rms[found - 1]:.2f} → {rms[found]:.2f} dB")

    # ---- bar phase ------------------------------------------------------------------
    cps = change_points(rms, low, drop_b)
    phase, counts = bar_phase_from_structure(cps)
    print(f"\nBAR PHASE  (from where the arrangement changes, at beat resolution)")
    print(f"  {'beats from drop':>16} {'bars':>8} {'strength':>10} {'residue mod 4':>15}")
    for rel, d in cps:
        print(f"  {rel:>+16} {rel / 4:>8.2f} {d:>10.2f} {int(rel) % 4:>15}")
    print(f"  residues: " + "  ".join(f"{p}:{c}" for p, c in enumerate(counts)))
    if max(counts) < len(cps) * 0.6:
        print(f"  → INCONCLUSIVE: changes are scattered across residues. This method "
              f"cannot see bar structure in this material.")
    else:
        print(f"  → {max(counts)}/{len(cps)} of the largest changes share one residue, so "
              f"that residue is the bar line.")
        print(f"    The drop sits {phase} beat(s) after a bar line"
              f"{'  — the drop IS the downbeat' if phase == 0 else ''}.")

    ev = section_edges(rms, low, drop_b)

    # ---- the section ----------------------------------------------------------------
    pb = plateau_beats(low, drop_b)
    declared = int(song["tuning"].get("carousel_beats", 0))
    print(f"\nTHE POST-DROP SECTION")
    print(f"  {'bar':>5} {'beats':>10} {'rms dB':>9} {'low':>8}")
    for b, r, lo_ in ev:
        if 0 <= b <= 10:
            print(f"  {b:>5} {b * 4:>+5}..{b * 4 + 3:>+4} {r:>9.2f} {lo_:>8.2f}"
                  f"{'   ← full-energy plateau ends here' if b * 4 == pb else ''}")
    print(f"\n  → full-energy plateau = {pb} beats = {pb // 4} bars from the drop")
    print(f"    song file declares carousel_beats: {declared}"
          f"   ({declared - pb:+d} beats)")
    end_phase = (declared + phase) % 4
    print(f"    a {declared}-beat carousel ends "
          f"{'ON a bar line' if end_phase == 0 else f'{end_phase} beat(s) into a bar'}")

    # ---- the energy curve, and whether it can supply intensity -----------------------
    seg = rms[drop_b:drop_b + declared]      # the carousel's own span, nothing past it
    rng = float(seg.max() - seg.min())
    print(f"\nTHE ENERGY CURVE, post-drop")
    print(f"  {' '.join(f'{v:.1f}' for v in seg[:declared])}")
    print(f"  dynamic range across the carousel: {rng:.2f} dB")
    if rng < 3.0:
        print(f"  → FLAT. This track cannot supply an intensity curve: it is a mastered")
        print(f"    drop that holds inside {rng:.1f}dB for the whole section. Escalation")
        print(f"    in a video cut to it has to come from the PICTURES. Correlating an")
        print(f"    authored intensity list against this would be reading noise.")

    for vid in a.against:
        inst = rtf.load(rtf.find_edit(vid))
        got = [it.get("intensity") for it in (inst.get("carousel") or {}).get("items", [])]
        if any(v is None for v in got):
            print(f"\n  {vid}: incomplete intensity list")
            continue
        print(f"\nCONGRUENCY — {vid} vs the track  (n={len(got)})")
        print(f"  authored : {' '.join(str(v) for v in got)}")
        if rng < 3.0:
            print(f"  not computed — see above. The track is flat to {rng:.1f}dB; any r "
                  f"here is a number about nothing.")
        else:
            want = quantize_intensity(rms[drop_b:drop_b + len(got)])
            print(f"  track    : {' '.join(str(v) for v in want)}")
            print(f"  pearson r {pearson(got, want):+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
