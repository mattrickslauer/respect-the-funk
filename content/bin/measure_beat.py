#!/usr/bin/env python3
"""Measure a track's BPM and locate the true downbeat near a stated drop.

    python3 measure_beat.py ../audio/track.mp3 --drop-ms 50000

Spectral-flux onset envelope → autocorrelation for tempo → phase fit for the beat grid.
Prints the BPM, the nearest on-grid downbeat to the stated drop, and the drift you'd
accumulate over the sequence if you cut on the stated value instead.

Requires: ffmpeg on PATH, numpy.
"""

import argparse
import subprocess
import sys

import numpy as np

SR = 22050
HOP = 256
NFFT = 1024


def decode(path: str, start: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", path,
           "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def onset_envelope(x: np.ndarray) -> np.ndarray:
    """Half-wave-rectified spectral flux, per frame."""
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


def comb_fit(env: np.ndarray, offset_s: float, lo=70.0, hi=180.0, step=0.02):
    """Joint tempo + phase fit: score a beat comb against the onset envelope.

    Autocorrelation alone gives a reliable *period* but a phase estimate that wanders,
    because a small tempo error accumulates across the window. Fitting both at once
    against the actual onsets does not — the comb only scores high when every predicted
    beat lands on a real transient.

    Returns (bpm, absolute time in seconds of one beat on the grid).
    """
    fps = SR / HOP
    n = len(env)
    best = (-np.inf, None, None)
    for bpm in np.arange(lo, hi, step):
        period = 60.0 / bpm * fps
        nbeats = int((n - 1) / period)
        if nbeats < 8:
            continue
        k = np.arange(nbeats)
        # every candidate phase, half-frame resolution
        phases = np.arange(0, period, 0.5)
        idx = np.rint(phases[:, None] + k[None, :] * period).astype(int)
        score = env[np.clip(idx, 0, n - 1)].sum(axis=1) / nbeats
        j = int(np.argmax(score))
        if score[j] > best[0]:
            best = (score[j], bpm, phases[j])
    _, bpm, ph = best
    return bpm, offset_s + ph / fps


def peaks(env: np.ndarray, lo: int, hi: int, min_sep_s=0.2) -> list[tuple[float, float]]:
    """(time, strength) of onset peaks in a frame range, strongest first."""
    fps = SR / HOP
    seg = env[lo:hi]
    out: list[tuple[float, float]] = []
    for i in np.argsort(seg)[::-1]:
        t = (lo + int(i)) / fps
        if any(abs(t - p) < min_sep_s for p, _ in out):
            continue
        out.append((t, float(seg[i])))
        if len(out) >= 24:
            break
    return out


def local_pulse(env: np.ndarray, center_s: float, radius_s=3.0) -> float:
    """Beat period in seconds, measured from onset spacing right at the drop.

    A full-track tempo sweep can't tell 125 BPM from its dotted-quarter alias at 83.33 —
    both combs score the same. The spacing between actual transients in the bars around
    the drop can: it is the pulse the arrangement is playing. Local beats global here.
    """
    fps = SR / HOP
    lo = max(0, int((center_s - radius_s) * fps))
    hi = min(len(env), int((center_s + radius_s) * fps))
    ts = sorted(t for t, s in peaks(env, lo, hi) if s > 1.0)
    diffs = [b - a for a, b in zip(ts, ts[1:]) if 0.25 <= b - a <= 1.05]
    if len(diffs) < 3:
        return 0.0
    return float(np.median(diffs))


def snap_to_transient(env: np.ndarray, target_s: float, radius_s=0.9) -> tuple[float, float]:
    """The strongest onset near `target_s` — a drop is a transient by definition."""
    fps = SR / HOP
    lo, hi = max(0, int((target_s - radius_s) * fps)), min(len(env), int((target_s + radius_s) * fps))
    if hi <= lo:
        return target_s, 0.0
    i = int(np.argmax(env[lo:hi]))
    return (lo + i) / fps, float(env[lo + i])


def downbeat(env: np.ndarray, anchor_s: float, period_s: float, bars=8) -> float:
    """Which of the four beats around `anchor_s` is the 1.

    Landing a title sequence on beat 4 instead of beat 1 sounds wrong even though every
    cut is technically on the beat. Scoring a bar-period comb at each of the four phases
    picks the one the arrangement accents.
    """
    fps = SR / HOP
    bar = period_s * 4
    k = np.arange(-bars, bars + 1)
    best, best_t = -np.inf, anchor_s
    for j in range(4):
        t0 = anchor_s + j * period_s
        idx = np.rint((t0 + k * bar) * fps).astype(int)
        idx = idx[(idx >= 0) & (idx < len(env))]
        s = float(env[idx].mean()) if idx.size else -np.inf
        if s > best:
            best, best_t = s, t0
    return best_t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--drop-ms", type=int, required=True)
    ap.add_argument("--window", type=float, default=600.0, help="seconds of audio analysed from 0")
    ap.add_argument("--cuts", type=int, default=9, help="how many post-drop cuts, for the drift figure")
    args = ap.parse_args()

    drop = args.drop_ms / 1000.0
    x = decode(args.audio, 0.0, args.window)      # whole track by default
    if x.size < NFFT * 4:
        print("not enough audio decoded", file=sys.stderr)
        return 1

    env = onset_envelope(x)
    gbpm, _ = comb_fit(env, 0.0)                  # global tempo family
    period = local_pulse(env, drop)               # the pulse actually playing at the drop
    lbpm = 60.0 / period if period else gbpm
    ratio = lbpm / gbpm

    # Local only overrules global when they differ by a *musical* factor — a genuine
    # metric alias (half-time, double-time, dotted-quarter). A few percent apart is just
    # noise in a median of a handful of onsets, and there the global comb is far more
    # precise: it is fitted over thousands of beats. Taking local there would bake in a
    # tempo error that compounds over every cut.
    ALIASES = {0.5: "half-time", 2 / 3: "dotted-quarter", 1.5: "dotted", 2.0: "double-time"}
    alias = next((n for r, n in ALIASES.items() if abs(ratio - r) < 0.04), None)
    bpm, why = (lbpm, f"local ({alias} alias)") if alias else (gbpm, "global")
    beat_ms = 60000.0 / bpm
    anchor, strength = snap_to_transient(env, drop)

    print(f"global comb    {gbpm:.2f} BPM")
    print(f"local pulse    {lbpm:.2f} BPM   ({ratio:.3f}× global)")
    if alias:
        print(f"  ⚠ genuine {alias} ambiguity — using local, measured at the drop itself")
    elif abs(ratio - 1) > 0.002:
        print(f"  local is within noise of global — using global, fitted over the whole track")
    print(f"using          {bpm:.2f} BPM via {why}   (beat {beat_ms:.0f}ms, bar {beat_ms * 4:.0f}ms)")
    print()
    db = downbeat(env, anchor - beat_ms / 1000, beat_ms / 1000)
    # the bar line nearest what the user said
    cut = db + round((drop - db) / (beat_ms * 4 / 1000)) * beat_ms * 4 / 1000

    print(f"stated drop    {args.drop_ms} ms")
    print(f"nearest onset  {anchor * 1000:.0f} ms   ({(anchor - drop) * 1000:+.0f} ms, strength {strength:.1f})")
    print(f"  ...which is {'the downbeat' if abs(anchor - cut) < 0.02 else 'a beat, NOT the downbeat'}")
    print()
    print("bar lines:")
    for m in range(-2, 3):
        t = cut + m * beat_ms * 4 / 1000
        print(f"  {t * 1000:8.0f} ms {'← nearest to stated drop' if m == 0 else ''}")
    print()
    print(f"→ music.bpm:     {bpm:.2f}")
    print(f"→ music.drop_ms: {round(cut * 1000)}")
    seq_ms = args.cuts * beat_ms
    off = abs(cut - drop) * 1000
    if off > 25:
        print(f"  cutting on the stated {args.drop_ms} would sit {off:.0f}ms off the beat, "
              f"and a fixed offset never washes out — every one of the "
              f"{args.cuts} cuts across {seq_ms / 1000:.1f}s stays that late.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
