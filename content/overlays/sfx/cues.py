#!/usr/bin/env python3
"""Place the sound palette against the cut, and render one stereo bed.

    python3 cues.py            # -> out/sfx/bed.wav, full length, ready to mix

## Cues are written in SCENE time, not timeline time

Every cue below says "0.9s into scene 03", never "31.5s into the video". The
scenes were stretched to fit what was actually said — scene 07 plays at 0.745x —
so a cue pinned to the timeline would drift off its own visual the moment anyone
re-reads a line and the alignment changes. Pinned to scene time it survives a
re-shoot: re-run align.py, re-run this, and every sound is still on its frame.

The conversion is the same arithmetic compose.py uses, imported from it rather
than copied, because two copies of a mapping like that disagree eventually.

## What is deliberately NOT scored

Speech. There is no cue on a word, an emphasis or a sentence end. Sound effects
that punctuate talking are the fastest way to make something feel like a corporate
explainer, and the voice is already carrying the argument. Everything here is
attached to a thing that visibly happens on screen — a detent landing, a worker
dying, the playhead moving — so the sound reads as the picture having a physical
presence rather than as decoration under the narration.

The loudest cue in the sheet sits about 14 dB under the dialogue.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from compose import PLAN, LEAD, TAIL          # noqa: E402  the one source of truth

SR = 48_000
SFX = HERE.parent / "out" / "sfx"
ALIGN = HERE.parent / "out" / "edit" / "align.json"

# Global trim on everything here, in dB. The palette is already normalised low;
# this is the one knob to turn if the bed feels loud against the voice.
TRIM = -3.0

# (scene stem, seconds into the scene, sound, gain dB)
CUES: list[tuple[str, float, str, float]] = [
    # 01 — a record exists, the world is enormous, it goes out, it comes back
    ("01-disappear",  0.25, "drop",       0),
    *[("01-disappear", 9.0 + i * 0.62, "tick-soft", -2) for i in range(6)],
    ("01-disappear", 16.2, "thunk",      -6),
    ("01-disappear", 21.0, "drop",       -2),
    ("01-disappear", 22.7, "pulse",      +2),

    # 02 — fire at everything, then pull it back to six
    ("02-spam",       0.35, "aperture",  -3),
    ("02-spam",       3.95, "tick",      -4),

    # 03 — three detents closing the dial, then the query lands
    ("03-subspace",   0.95, "tick",       0),
    ("03-subspace",   2.35, "tick",       0),
    ("03-subspace",   3.75, "tick",       0),
    ("03-subspace",   0.90, "aperture",  -6),
    ("03-subspace",   5.05, "tick-soft", -2),
    ("03-subspace",   5.85, "drop",      -5),
    *[("03-subspace", 7.05 + i * 0.22, "tick-soft", -8) for i in range(3)],

    # 04 — the divisions appear, one wedge is isolated, then the sweep
    ("04-tenant",     0.45, "tick",      -3),
    ("04-tenant",     1.65, "tick-soft",  0),
    *[("04-tenant", 4.7 + i * 0.6, "pulse", 0) for i in range(5)],

    # 05 — the concession. The tick is the point; give it room.
    ("05-pgvector",   1.45, "accept",    -3),
    ("05-pgvector",   2.95, "tick-soft", -4),

    # 06 — facts raining onto the index
    *[("06-filling", 0.6 + i * 0.86, "shimmer", -3 - (i % 3)) for i in range(10)],

    # 07 — the bus waking each agent, then half of them die, then it finishes
    *[("07-fleet", 0.55 + i * 0.341, "pulse", 0) for i in range(12)],
    ("07-fleet",      7.65, "thunk",      0),
    ("07-fleet",     13.85, "accept",    -4),

    # 08 — two workers, one refused, one through
    ("08-lease",      0.55, "tick-soft", -3),
    ("08-lease",      2.05, "tick",      -2),
    ("08-lease",      2.25, "tick",      -2),
    ("08-lease",      4.45, "refuse",     0),
    ("08-lease",      5.85, "accept",    -2),

    # 09 — out, back, and into the one slot that matches
    ("09-token",      0.35, "tick-soft", -3),
    ("09-token",      4.95, "tick",      -1),
    ("09-token",      5.15, "accept",    -6),

    # 10 — rows leave for their regions, a region is lost, it keeps answering
    ("10-residency",  2.25, "tick-soft", -2),
    ("10-residency",  3.75, "tick-soft", -2),
    ("10-residency",  8.25, "aperture",  -8),
    ("10-residency", 13.25, "thunk",     -1),
    ("10-residency", 16.65, "accept",    -3),

    # 11 — it climbs to the ceiling, asks, and is approved
    ("11-money",      0.45, "tick-soft", -3),
    ("11-money",      3.45, "tick",      -2),
    ("11-money",      6.05, "accept",     0),

    # 12 — the centrepiece. The rewind is the loudest cue in the film.
    ("12-replay",     0.35, "drop",      -3),
    *[("12-replay", 5.4 + i * 1.4, "shimmer", -6) for i in range(4)],
    ("12-replay",    11.25, "tick-soft", -2),
    ("12-replay",    12.25, "tick",      -1),
    ("12-replay",    16.40, "rewind",    +1),
    ("12-replay",    22.20, "tick-soft", -4),
    ("12-replay",    27.45, "accept",    +1),

    # 13 — five roles collapse into one, then to nothing, then the address
    *[("13-close", 0.35 + i * 0.22, "tick-soft", -4) for i in range(5)],
    ("13-close",      4.25, "thunk",     -4),
    ("13-close",      6.65, "settle",    -2),
    ("13-close",      8.65, "accept",    -1),
]


def load(name: str) -> np.ndarray:
    with wave.open(str(SFX / f"{name}.wav"), "rb") as w:
        raw = w.readframes(w.getnframes())
        n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
    assert sw == 3, f"{name}: expected 24-bit"
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
    v = np.where(v & 0x800000, v - 0x1000000, v).astype(np.float64) / (2 ** 23)
    return v.reshape(n, ch)


def windows() -> dict[str, tuple[float, float]]:
    """Scene stem -> (start, seconds-per-authored-second). Mirrors compose.py."""
    align = json.loads(ALIGN.read_text())
    out: dict[str, tuple[float, float]] = {}
    for stem, paras in PLAN:
        rows = [align[i] for i in paras if align[i]]
        if not rows:
            continue
        start = max(0.0, min(r["start"] for r in rows) - LEAD)
        end = max(r["end"] for r in rows) + TAIL
        out[stem] = (start, end - start)
    return out


def main() -> int:
    if not ALIGN.exists():
        print("run align.py first", file=sys.stderr)
        return 1
    wins = windows()

    # authored durations, read off the scene files so this cannot drift
    authored: dict[str, float] = {}
    for stem, _ in PLAN:
        src = (HERE.parent / "scenes" / f"{stem}.html").read_text()
        import re
        m = re.search(r"dur:\s*([0-9.]+)", src)
        authored[stem] = float(m.group(1)) if m else 1.0

    total = int(SR * (max(s + d for s, d in wins.values()) + 3.0))
    bed = np.zeros((total, 2))
    cache: dict[str, np.ndarray] = {}
    placed = dropped = 0

    for stem, st, name, gain in CUES:
        if stem not in wins:
            dropped += 1
            continue
        start, span = wins[stem]
        scale = span / authored[stem]
        at = start + st * scale
        i = int(at * SR)
        if i < 0 or i >= total:
            dropped += 1
            continue
        if name not in cache:
            cache[name] = load(name)
        x = cache[name] * (10 ** ((gain + TRIM) / 20))
        j = min(total, i + len(x))
        bed[i:j] += x[: j - i]
        placed += 1

    peak = float(np.max(np.abs(bed)))
    print(f"{placed} cues placed, {dropped} dropped · bed peak "
          f"{20 * np.log10(max(peak, 1e-9)):.1f} dBFS")

    # Overlapping cues can stack; catch it here rather than discovering it in the
    # mix. Scale the whole bed rather than limiting — these are transients and a
    # limiter would flatten exactly the attacks that make them read.
    if peak > 0.5:
        bed *= 0.5 / peak
        print(f"  scaled down to -6 dBFS (cues were stacking)")

    out = SFX / "bed.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(SR)
        q = (np.clip(bed, -1, 1) * (2 ** 23 - 1)).astype(np.int32).reshape(-1)
        w.writeframes(b"".join(v.tobytes()[:3] for v in q))
    print(f"{out.relative_to(HERE.parent)}  {len(bed) / SR:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
