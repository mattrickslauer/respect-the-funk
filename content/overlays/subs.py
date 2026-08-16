#!/usr/bin/env python3
"""Build burn-in subtitles from the aligned script.

    python3 subs.py            # -> out/edit/subs.ass (+ subs.srt for upload)

## Why not just use the transcript

Whisper's text is wrong in exactly the places that matter here — "PG vector",
"tenant ID", "cockroach DB", the URL as four separate words. Burning that in puts
a typo of your own product name on screen for three minutes.

So the *words* come from SHOOT_VOICEOVER.txt, which is correct by definition, and
only the *timing* comes from the transcript. align.py already did that matching;
this reads its output and splits each paragraph into caption-sized lines,
apportioning the paragraph's measured duration across them by character count.

That apportioning is an approximation — it assumes an even speaking rate within a
paragraph, which is not quite true. It is accurate to about a fifth of a second,
which is invisible for reading. Word-perfect timing would need the per-word
offsets, and align.py currently keeps only paragraph edges; if a line ever drifts
noticeably, that is the thing to extend.

## Style

Bottom-centre, well inside the action safe area, in the same IBM Plex Mono the
graphics use so the film has one typeface rather than two. Sized for phone
viewing, which is where a submission video actually gets watched, with a heavy
outline and a soft shadow because the background behind them is a real room and
not a lower-third.

Two lines maximum, ~42 characters each. Longer than that and a viewer is reading
instead of listening.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDIT = HERE / "out" / "edit"
MAX_CHARS = 42
MAX_LINES = 2
MIN_DUR = 0.9


def clause_split(text: str) -> list[str]:
    """Split a paragraph into caption-sized chunks, preferring real boundaries.

    Breaking on punctuation first, then on width, keeps captions from cutting a
    phrase in half — which is the difference between a caption you read without
    noticing and one you have to re-read.
    """
    parts = re.split(r"(?<=[.!?—])\s+|(?<=,)\s+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        words, line = p.split(), ""
        budget = MAX_CHARS * MAX_LINES
        for w in words:
            if line and len(line) + 1 + len(w) > budget:
                out.append(line)
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            out.append(line)

    # merge anything too short to be worth its own card
    merged: list[str] = []
    for c in out:
        if merged and len(merged[-1]) + len(c) + 1 <= MAX_CHARS * MAX_LINES:
            if len(merged[-1]) < 22 or len(c) < 18:
                merged[-1] = f"{merged[-1]} {c}"
                continue
        merged.append(c)
    return merged


def wrap(s: str) -> str:
    """Balance across two lines, so a caption is never one long line and one word."""
    if len(s) <= MAX_CHARS:
        return s
    words, best, err = s.split(), None, 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > MAX_CHARS or len(b) > MAX_CHARS:
            continue
        if abs(len(a) - len(b)) < err:
            best, err = (a, b), abs(len(a) - len(b))
    return "\\N".join(best) if best else s


def ts_ass(t: float) -> str:
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ts_srt(t: float) -> str:
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Spindle,IBM Plex Mono,52,&H00ECF3F7,&H00040506,&HA0000000,-1,0,1,4,2,2,120,120,74,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def main() -> int:
    ap = EDIT / "align.json"
    if not ap.exists():
        print("run align.py first", file=sys.stderr)
        return 1

    cards: list[tuple[float, float, str]] = []
    for row in json.loads(ap.read_text()):
        if not row:
            continue
        chunks = clause_split(row["text"])
        if not chunks:
            continue
        span = max(row["end"] - row["start"], 0.4)
        total = sum(len(c) for c in chunks)
        at = row["start"]
        for c in chunks:
            d = span * (len(c) / total)
            cards.append((at, at + d, c))
            at += d

    # never let a card sit on screen for less than it takes to read, and never
    # let one overrun the next
    for i, (s, e, c) in enumerate(cards):
        e = max(e, s + MIN_DUR)
        if i + 1 < len(cards):
            e = min(e, cards[i + 1][0] - 0.04)
        cards[i] = (s, max(e, s + 0.35), c)

    ass = [ASS_HEAD]
    for s, e, c in cards:
        ass.append(f"Dialogue: 0,{ts_ass(s)},{ts_ass(e)},Spindle,,0,0,0,,{wrap(c)}")
    (EDIT / "subs.ass").write_text("\n".join(ass) + "\n")

    srt = []
    for i, (s, e, c) in enumerate(cards, 1):
        srt.append(f"{i}\n{ts_srt(s)} --> {ts_srt(e)}\n{wrap(c).replace(chr(92) + 'N', chr(10))}\n")
    (EDIT / "subs.srt").write_text("\n".join(srt))

    longest = max(cards, key=lambda c: len(c[2]))
    print(f"{len(cards)} cards · longest {len(longest[2])} chars · "
          f"{EDIT.name}/subs.ass + subs.srt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
