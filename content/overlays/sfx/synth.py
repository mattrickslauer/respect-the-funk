#!/usr/bin/env python3
"""Synthesise the sound palette. No sample library, no downloads — just numpy.

    python3 synth.py            # writes out/sfx/*.wav at 48k stereo float->pcm24

## The brief these are written to

The picture is an instrument reading: copper lines on a polar grid, drawn like an
oscilloscope. The sound has to belong to that. So nothing here is a UI sound —
no glassy notification pings, no whooshes with a rising shepard tone, none of the
motion-graphics library clichés. The references are physical: a relay closing, a
detent clicking into place, a soft mallet on wood, tape spooling back.

Three rules, and every sound below obeys them:

1. **Short, and gone.** Almost everything is under 400ms. A sound that rings on
   competes with the voice, and the voice is the product.
2. **Dark.** Energy sits under about 6kHz. Bright sounds read as "app" and also
   collide with sibilance in the speech, which is the one band already crowded.
3. **Quiet by construction.** Everything is normalised to a peak well under full
   scale, per-sound, so the cue sheet can place things without a mixing pass.
   The loudest thing here peaks at -14 dBFS.

## Why synthesis rather than samples

Partly because there is no library to hand. But mostly because these have to be
*matched* to the graphics — the aperture in scene 03 closes over a duration the
cue sheet knows, and a sweep synthesised to exactly that length lands with it,
where a stock whoosh would need trimming and would still have the wrong shape.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np

SR = 48_000
OUT = Path(__file__).resolve().parent.parent / "out" / "sfx"


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------

def t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def env(n: int, attack: float, decay: float, curve: float = 2.5) -> np.ndarray:
    """Percussive envelope: near-instant rise, exponential fall.

    `curve` is what stops these sounding synthetic. A linear decay reads as a
    fade; an exponential one reads as something that was struck.
    """
    a = max(1, int(SR * attack))
    d = max(1, n - a)
    return np.concatenate([
        np.linspace(0, 1, a) ** 0.6,
        np.exp(-curve * np.linspace(0, 1, d) * (1 / max(decay, 1e-6)) * decay * 5),
    ])[:n]


def band(x: np.ndarray, lo: float | None, hi: float | None) -> np.ndarray:
    """FFT brick-wall with a raised-cosine skirt.

    A brick wall alone rings audibly on transients this short; the skirt costs
    four lines and removes it.
    """
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    m = np.ones_like(f)
    if lo:
        w = np.clip((f - lo * 0.5) / (lo * 0.5), 0, 1)
        m *= 0.5 - 0.5 * np.cos(np.pi * w)
    if hi:
        w = np.clip((hi * 1.6 - f) / (hi * 0.6), 0, 1)
        m *= 0.5 - 0.5 * np.cos(np.pi * w)
    return np.fft.irfft(X * m, n)


def noise(dur: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(int(SR * dur))


def sweep(dur: float, f0: float, f1: float, curve: float = 1.0) -> np.ndarray:
    """Sine whose frequency glides f0 -> f1. Phase is integrated, not stepped —
    stepping it produces a click at every sample boundary."""
    x = t(dur)
    u = (x / max(x[-1], 1e-9)) ** curve
    freq = f0 * (f1 / f0) ** u
    return np.sin(2 * np.pi * np.cumsum(freq) / SR)


def peak_to(x: np.ndarray, db: float) -> np.ndarray:
    p = np.max(np.abs(x))
    return x * (10 ** (db / 20) / p) if p > 0 else x


def stereo(x: np.ndarray, spread: float = 0.0) -> np.ndarray:
    """Mono to stereo. `spread` delays one side by a few samples — a Haas trick
    that widens without any of the phase weirdness of a real chorus."""
    if spread <= 0:
        return np.stack([x, x], axis=1)
    d = int(SR * spread / 1000)
    l = np.concatenate([x, np.zeros(d)])
    r = np.concatenate([np.zeros(d), x])
    return np.stack([l, r], axis=1)


def save(name: str, x: np.ndarray) -> None:
    if x.ndim == 1:
        x = stereo(x)
    x = np.clip(x, -1, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)                       # 24-bit: these get mixed, not shipped
        w.setframerate(SR)
        q = (x * (2 ** 23 - 1)).astype(np.int32)
        w.writeframes(b"".join(
            struct.pack("<i", v)[:3] for v in q.reshape(-1)))
    peak = 20 * np.log10(max(np.max(np.abs(x)), 1e-9))
    print(f"{name:14} {len(x) / SR:5.2f}s  peak {peak:6.1f} dBFS")


# --------------------------------------------------------------------------
# the palette
# --------------------------------------------------------------------------

def make() -> None:
    # TICK — a detent dropping into place. Two damped resonances plus a scrap of
    # noise for the contact itself. Used per filter closing, per dot lighting.
    n = int(SR * 0.06)
    x = (np.sin(2 * np.pi * 1850 * t(0.06)) * 0.6
         + np.sin(2 * np.pi * 2790 * t(0.06)) * 0.25
         + band(noise(0.06, 1), 1200, 5200) * 0.3)
    save("tick", peak_to(x * env(n, 0.0004, 0.055, 3.4), -19))

    # TICK-SOFT — the same gesture an octave down and blunter, for anything
    # ambient. This is the one that repeats most, so it is the quietest.
    n = int(SR * 0.09)
    x = (np.sin(2 * np.pi * 820 * t(0.09)) * 0.7
         + band(noise(0.09, 2), 500, 2400) * 0.25)
    save("tick-soft", peak_to(x * env(n, 0.001, 0.08, 3.0), -26))

    # APERTURE — the dial closing in scene 03. Band-passed noise whose centre
    # falls as the sector narrows, so the sound literally tracks the picture.
    dur = 0.75
    nz = noise(dur, 3)
    seg = 24
    out = np.zeros(int(SR * dur))
    edges = np.linspace(0, len(out), seg + 1).astype(int)
    for i in range(seg):
        u = i / (seg - 1)
        c = 3800 * (700 / 3800) ** u
        piece = band(nz[edges[i]:edges[i + 1]], c * 0.6, c * 1.55)
        out[edges[i]:edges[i + 1]] = piece
    shape = np.sin(np.pi * np.linspace(0, 1, len(out))) ** 1.4
    save("aperture", peak_to(out * shape, -21))

    # THUNK — a soft mallet on something wooden. For kill -9, and for the refusal.
    n = int(SR * 0.3)
    body = sweep(0.3, 190, 62, curve=0.35)
    click = band(noise(0.012, 4), 900, 4200)
    x = body * env(n, 0.001, 0.26, 2.2)
    x[:len(click)] += click * 0.55
    save("thunk", peak_to(band(x, 30, 3000), -15))

    # REFUSE — a minor second falling. Deliberately slightly sour; this is the
    # lease being lost and it should not feel neutral.
    dur = 0.26
    n = int(SR * dur)
    x = (np.sin(2 * np.pi * 466 * t(dur)) * 0.6
         + np.sin(2 * np.pi * 440 * t(dur)) * 0.5
         + np.sin(2 * np.pi * 932 * t(dur)) * 0.12)
    save("refuse", peak_to(band(x * env(n, 0.004, 0.22, 3.0), 120, 3500), -20))

    # ACCEPT — a fifth rising, soft attack, longer tail. Ticks and approvals.
    dur = 0.55
    n = int(SR * dur)
    e = env(n, 0.012, 0.5, 1.8)
    x = (np.sin(2 * np.pi * 587.33 * t(dur)) * 0.55
         + np.sin(2 * np.pi * 880.00 * t(dur)) * 0.4
         + np.sin(2 * np.pi * 1174.66 * t(dur)) * 0.15)
    save("accept", peak_to(band(x * e, 200, 5000), -20))

    # REWIND — the centrepiece. Tape spooling back: a falling glide under a
    # reversed noise swell, so it pulls backwards rather than pushing forward.
    dur = 1.35
    n = int(SR * dur)
    glide = sweep(dur, 620, 155, curve=0.8)
    swell = band(noise(dur, 5), 300, 2600)[::-1] * np.linspace(0.15, 1.0, n) ** 2
    e = np.concatenate([np.linspace(0, 1, int(n * 0.12)) ** 0.5,
                        np.linspace(1, 0.25, n - int(n * 0.12))])
    x = (glide * 0.75 + swell * 0.5) * e
    save("rewind", peak_to(band(x, 60, 4000), -17))

    # DROP — the record arriving, and the disc blooming. Sub with a soft knock.
    dur = 0.6
    n = int(SR * dur)
    x = sweep(dur, 120, 46, curve=0.5) * env(n, 0.004, 0.55, 1.9)
    knock = band(noise(0.04, 7), 150, 1200)
    x[:len(knock)] += knock * env(len(knock), 0.001, 0.035, 3.0) * 0.4
    save("drop", peak_to(band(x, 25, 2200), -14))

    # SHIMMER — facts landing on the index. Detuned high partials, fast decay,
    # stereo-spread so a run of them widens the field.
    dur = 0.34
    n = int(SR * dur)
    rng = np.random.default_rng(8)
    x = np.zeros(n)
    for f in (2960, 3730, 4430, 5270):
        x += np.sin(2 * np.pi * f * (1 + rng.uniform(-0.004, 0.004)) * t(dur)) * rng.uniform(0.5, 1)
    save("shimmer", stereo(peak_to(x * env(n, 0.002, 0.3, 3.6), -25), spread=7))

    # PULSE — the fleet's heartbeat, one hand-off. Barely there on purpose: it
    # repeats a dozen times in scene 07 and anything with character would grate.
    dur = 0.14
    n = int(SR * dur)
    x = np.sin(2 * np.pi * 320 * t(dur)) * env(n, 0.006, 0.12, 2.6)
    save("pulse", peak_to(band(x, 120, 1400), -28))

    # SETTLE — a long low note under the close, for the collapse to one node.
    dur = 2.2
    n = int(SR * dur)
    x = (np.sin(2 * np.pi * 110 * t(dur)) * 0.6
         + np.sin(2 * np.pi * 164.81 * t(dur)) * 0.3
         + np.sin(2 * np.pi * 220 * t(dur)) * 0.18)
    e = np.concatenate([np.linspace(0, 1, int(n * 0.18)) ** 0.7,
                        np.linspace(1, 0, n - int(n * 0.18)) ** 1.6])
    save("settle", peak_to(band(x * e, 50, 1200), -22))


if __name__ == "__main__":
    make()
