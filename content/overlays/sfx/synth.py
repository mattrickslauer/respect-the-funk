#!/usr/bin/env python3
"""Synthesise the sound palette. No sample library, no downloads — just numpy.

    python3 synth.py            # writes out/sfx/*.wav at 48k stereo float->pcm24

## The brief these are written to

The picture is an instrument reading: copper lines on a polar grid, drawn like an
oscilloscope. The sound has to belong to that. So nothing here is a UI sound —
no glassy notification pings, no rising shepard whooshes, none of the
motion-graphics library clichés. The references are physical: a relay closing, a
detent clicking into place, a soft mallet on wood, tape spooling back.

### The rule that matters, learned the hard way

**Everything lives above 8kHz or below 90Hz, and the middle is carved out.**

The first version of this file said the opposite — "dark, energy under 6kHz,
because bright reads as app". That was exactly wrong and it made the whole bed
inaudible. 300Hz to 4kHz *is* the speech band; a cue placed there is competing
with the one signal it must never compete with, and simultaneous masking means
anything more than a few dB down in-band is not quiet, it is *gone*. Measured on
the real mix, cues were sitting 9-13 dB under the dialogue inside its own
frequency range. Mathematically present, inaudible in practice, and turning them
up would only have made them fight the voice instead.

So the palette occupies the two places speech is not:

  * **Air, 8-15kHz.** The primary band, because it survives a laptop speaker and
    a phone. Speech has almost no energy up here — only sibilance, which is
    transient and narrow.
  * **Sub, 40-90Hz.** Reinforcement, for weight on a system that can reproduce
    it. Deliberately never the *only* content in a sound, because half the people
    watching will hear none of it.

`carve()` below notches 300Hz-3.5kHz out of everything as a final stage, so even
the sounds that need a little midrange body cannot creep into the voice.

Two rules survive from the first pass:

1. **Short, and gone.** Almost everything is under 400ms.
2. **Normalised per-sound**, so the cue sheet places things without a mixing pass.

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


def carve(x: np.ndarray, depth_db: float = -13.0) -> np.ndarray:
    """Notch the speech band out of a finished sound.

    The last stage on everything. Whatever midrange a sound needs for character,
    it does not get to keep the part that overlaps the dialogue — 300Hz to 3.5kHz
    comes down by `depth_db`, with cosine skirts so the notch is not audible as a
    filter.

    This is what makes the bed sit *beside* the voice instead of underneath it.
    """
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = 10 ** (depth_db / 20)
    lo, hi = 300.0, 3500.0
    m = np.ones_like(f)
    rise = np.clip((f - lo * 0.45) / (lo * 0.55), 0, 1)
    fall = np.clip((hi * 2.1 - f) / (hi * 1.1), 0, 1)
    notch = (0.5 - 0.5 * np.cos(np.pi * rise)) * (0.5 - 0.5 * np.cos(np.pi * fall))
    m = 1.0 - (1.0 - g) * notch
    return np.fft.irfft(X * m, n)


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
    """The palette. Every sound ends with carve() and lands in air and/or sub.

    Levels are much hotter than the first pass: the loudest cue now peaks at
    -9 dBFS against dialogue peaking at -1.7. In-band that would be far too much,
    but these barely touch the voice's frequencies, so they read clearly without
    ever fighting it.
    """

    # TICK — a detent dropping in. Now a contact click up in the air band rather
    # than a 1.8kHz resonance sitting on top of a vowel.
    n = int(SR * 0.05)
    x = (band(noise(0.05, 1), 7000, 13500) * 1.0
         + np.sin(2 * np.pi * 9400 * t(0.05)) * 0.35
         + np.sin(2 * np.pi * 62 * t(0.05)) * 0.5)          # a little floor under it
    save("tick", peak_to(carve(x * env(n, 0.0003, 0.045, 3.6)), -13))

    # TICK-SOFT — the ambient repeat. Blunter and lower in the air band.
    n = int(SR * 0.075)
    x = (band(noise(0.075, 2), 5200, 9500) * 0.9
         + np.sin(2 * np.pi * 58 * t(0.075)) * 0.4)
    save("tick-soft", peak_to(carve(x * env(n, 0.0008, 0.07, 3.2)), -18))

    # APERTURE — the dial closing. The sweep now runs DOWN through the air band
    # (11k -> 3.4k) so it tracks the picture without crossing into the voice.
    dur = 0.75
    nz = noise(dur, 3)
    seg = 24
    out = np.zeros(int(SR * dur))
    edges = np.linspace(0, len(out), seg + 1).astype(int)
    for i in range(seg):
        u = i / (seg - 1)
        c = 11000 * (3400 / 11000) ** u
        out[edges[i]:edges[i + 1]] = band(nz[edges[i]:edges[i + 1]], c * 0.72, c * 1.5)
    shape = np.sin(np.pi * np.linspace(0, 1, len(out))) ** 1.3
    sub = np.sin(2 * np.pi * 52 * t(dur)) * shape * 0.35
    save("aperture", peak_to(carve(out * shape + sub), -14))

    # THUNK — the mallet. Sub body with an air transient; the wooden midrange
    # that used to carry it is exactly what the voice was eating.
    dur = 0.34
    n = int(SR * dur)
    body = sweep(dur, 96, 44, curve=0.4) * env(n, 0.0015, 0.3, 2.1)
    x = body * 1.0
    # A longer, louder air transient than the sub body strictly needs. The sub
    # carries this sound on a system that can reproduce 45Hz; on a laptop the
    # only thing anyone hears IS this click, so it has to survive alone.
    click = band(noise(0.055, 4), 5000, 14000)
    x[:len(click)] += click * env(len(click), 0.0004, 0.05, 2.6) * 1.5
    save("thunk", peak_to(carve(x), -10))

    # REFUSE — still sour, but the dissonance now sits in the sub as a beat
    # between two close low tones, with a hard air tick on top.
    dur = 0.3
    n = int(SR * dur)
    e = env(n, 0.002, 0.26, 2.6)
    x = (np.sin(2 * np.pi * 73 * t(dur)) * 0.9
         + np.sin(2 * np.pi * 78 * t(dur)) * 0.85) * e
    tick_ = band(noise(0.025, 11), 6000, 12000)
    x[:len(tick_)] += tick_ * env(len(tick_), 0.0004, 0.022, 3.0) * 0.7
    save("refuse", peak_to(carve(x), -12))

    # ACCEPT — a rising bell, moved up an octave and a half into the air band,
    # over a soft sub. Reads as "yes" without occupying a single vowel.
    dur = 0.6
    n = int(SR * dur)
    e = env(n, 0.006, 0.55, 1.7)
    x = (np.sin(2 * np.pi * 8800 * t(dur)) * 0.55
         + np.sin(2 * np.pi * 11700 * t(dur)) * 0.4
         + np.sin(2 * np.pi * 13200 * t(dur)) * 0.2) * e
    x += np.sin(2 * np.pi * 66 * t(dur)) * env(n, 0.004, 0.5, 2.0) * 0.7
    save("accept", peak_to(carve(x), -12))

    # REWIND — the centrepiece, and the one sound whose LENGTH is load-bearing.
    #
    # It was 1.35s against a playhead that travels for 5.2s, so it stopped dead a
    # quarter of the way through the move and left the rest of the animation in
    # silence. A sound that ends before its picture does reads as a mistake, not
    # as restraint. This now runs the full length of the rewind.
    #
    # Held that long, a plain glide gets boring, so it spools: tape-transport
    # ticks that slow down as the playhead approaches the decision, which is also
    # what tells the ear the movement is decelerating rather than just long.
    dur = 5.2
    n = int(SR * dur)
    tt = t(dur)
    glide = sweep(dur, 110, 34, curve=0.7)
    swell = band(noise(dur, 5), 6500, 15000)[::-1] * np.linspace(0.08, 1.0, n) ** 1.6

    spool = np.zeros(n)
    click = band(noise(0.012, 21), 7000, 14000)
    click = click * env(len(click), 0.0003, 0.010, 3.4)
    at, gap = 0.10, 0.085
    while at < dur - 0.05:
        i = int(at * SR)
        j = min(n, i + len(click))
        # each tick a touch quieter as the transport slows
        spool[i:j] += click[: j - i] * (1.0 - 0.55 * (at / dur))
        gap *= 1.075                      # decelerating
        at += gap

    e = np.concatenate([np.linspace(0, 1, int(n * 0.05)) ** 0.5,
                        np.ones(int(n * 0.75)),
                        np.linspace(1, 0, n - int(n * 0.05) - int(n * 0.75)) ** 1.4])
    x = (glide * 0.9 + swell * 0.55 + spool * 0.8) * e[:n]
    save("rewind", peak_to(carve(x), -11))

    # DROP — the record arriving. Pure sub with an air knock so it is still
    # audible on a laptop that reproduces nothing below 150Hz.
    dur = 0.62
    n = int(SR * dur)
    x = sweep(dur, 104, 40, curve=0.45) * env(n, 0.003, 0.56, 1.8)
    # Same reasoning as thunk: the sub is the weight, the knock is what a phone
    # speaker actually plays back.
    knock = band(noise(0.06, 7), 4800, 13000)
    x[:len(knock)] += knock * env(len(knock), 0.0006, 0.055, 2.5) * 1.35
    save("drop", peak_to(carve(x), -10))

    # SHIMMER — facts landing. Already lived up here; pushed higher and widened.
    dur = 0.32
    n = int(SR * dur)
    rng = np.random.default_rng(8)
    x = np.zeros(n)
    for f in (9200, 10900, 12600, 14100):
        x += np.sin(2 * np.pi * f * (1 + rng.uniform(-0.004, 0.004)) * t(dur)) * rng.uniform(0.5, 1)
    save("shimmer", stereo(peak_to(carve(x * env(n, 0.0015, 0.28, 3.6)), -17), spread=7))

    # PULSE — the fleet heartbeat. A sub blip with a whisper of air. It repeats a
    # dozen times, so it stays the quietest thing in the palette.
    dur = 0.13
    n = int(SR * dur)
    x = np.sin(2 * np.pi * 68 * t(dur)) * env(n, 0.003, 0.11, 2.6)
    hi = band(noise(0.015, 12), 7000, 13000)
    x[:len(hi)] += hi * env(len(hi), 0.0004, 0.013, 3.0) * 0.4
    save("pulse", peak_to(carve(x), -20))

    # SETTLE — the long note under the close. A sub drone with an air tail.
    dur = 2.2
    n = int(SR * dur)
    e = np.concatenate([np.linspace(0, 1, int(n * 0.18)) ** 0.7,
                        np.linspace(1, 0, n - int(n * 0.18)) ** 1.6])
    x = (np.sin(2 * np.pi * 55 * t(dur)) * 0.9
         + np.sin(2 * np.pi * 82.4 * t(dur)) * 0.45) * e
    air = band(noise(dur, 13), 8000, 14000) * e * 0.18
    save("settle", peak_to(carve(x + air), -14))


if __name__ == "__main__":
    make()
