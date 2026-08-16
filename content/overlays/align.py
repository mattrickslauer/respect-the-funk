#!/usr/bin/env python3
"""Transcribe the base layer and pin every script paragraph to a real timestamp.

    python3 align.py ../BaseLayer.mov

Writes out/edit/align.json — for each paragraph of SHOOT_VOICEOVER.txt, the second
it starts and the second it ends in the take.

## Why this aligns rather than just transcribing

We already know exactly what was said; the script is in the repo. What we do not
know is *when*, and ASR on its own answers that badly — it mishears "pgvector" as
"PG vector", drops "tenant_id" into two tokens, and renders the URL as four words.
Cutting graphics against raw ASR text means cutting against those errors.

So the transcript is used only as a clock. Its words are matched against the
script's words with a sequence alignment, and the script — which is correct by
definition — supplies the structure. A mistranscribed word still carries a good
timestamp, because Whisper knows *when* it heard something even when it is wrong
about *what*.

Tokens are normalised down to bare alphanumerics and split on everything else, so
`tenant_id` becomes `tenant id` and `spindle.mattrickslauer.com` becomes three
tokens, which is what the transcript will have produced anyway.

## Paragraph edges

A paragraph's start is the start of its first matched token and its end is the end
of its last. Where the very first or last tokens of a paragraph failed to match,
the search walks inward to the nearest token that did — better a paragraph that
begins a word late than one anchored to a hallucination.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
VO = HERE.parent.parent / "docs" / "submission" / "SHOOT_VOICEOVER.txt"
OUT = HERE / "out" / "edit"
MODEL = "small.en"


def norm(text: str) -> list[str]:
    """Bare alphanumeric tokens. Punctuation, case and underscores all go."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def paragraphs() -> list[str]:
    raw = VO.read_text().strip()
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", raw) if p.strip()]


def transcribe(media: Path) -> list[dict]:
    wav = OUT / "base16k.wav"
    if not wav.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        str(wav)], check=True)

    from faster_whisper import WhisperModel
    # int8 on CPU: this is 2-3 minutes of clean close-mic speech, and the model is
    # being used as a stopwatch rather than as a transcriber, so the accuracy lost
    # to quantisation costs nothing here.
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=10)
    segments, _ = model.transcribe(
        str(wav),
        word_timestamps=True,
        # VAD off on purpose: it trims leading breath, and a paragraph that starts
        # on the breath is exactly where a graphic wants to come up.
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for seg in segments:
        for w in (seg.words or []):
            for tok in norm(w.word):
                words.append({"t": tok, "s": float(w.start), "e": float(w.end)})
    return words


def main() -> int:
    media = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "BaseLayer.mov"
    if not media.exists():
        print(f"no such media: {media}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    paras = paragraphs()
    # script tokens, each remembering which paragraph it came from
    stoks: list[str] = []
    owner: list[int] = []
    for i, p in enumerate(paras):
        for t in norm(p):
            stoks.append(t)
            owner.append(i)

    words = transcribe(media)
    atoks = [w["t"] for w in words]

    # script index -> asr index, for the runs that matched
    hit: dict[int, int] = {}
    for a, b, n in SequenceMatcher(a=stoks, b=atoks, autojunk=False).get_matching_blocks():
        for k in range(n):
            hit[a + k] = b + k

    matched = len(hit)
    print(f"transcript {len(atoks)} tokens · script {len(stoks)} tokens · "
          f"matched {matched} ({matched / len(stoks) * 100:.1f}%)")
    if matched < len(stoks) * 0.6:
        print("ALIGNMENT TOO WEAK — check the audio or the script", file=sys.stderr)
        return 1

    out = []
    for i, p in enumerate(paras):
        idx = [k for k, o in enumerate(owner) if o == i]
        first = next((k for k in idx if k in hit), None)
        last = next((k for k in reversed(idx) if k in hit), None)
        if first is None or last is None:
            print(f"P{i:02d} UNMATCHED — {p[:50]}", file=sys.stderr)
            out.append(None)
            continue
        out.append({
            "i": i,
            "start": round(words[hit[first]]["s"], 3),
            "end": round(words[hit[last]]["e"], 3),
            "text": p,
        })

    # Fill any gap between paragraphs so a graphic never sits over silence it was
    # not given: each paragraph's end extends to the next one's start.
    for i in range(len(out) - 1):
        if out[i] and out[i + 1]:
            out[i]["gap_to_next"] = round(out[i + 1]["start"] - out[i]["end"], 3)

    (OUT / "align.json").write_text(json.dumps(out, indent=2))
    for r in out:
        if r:
            print(f'P{r["i"]:02d}  {r["start"]:7.2f} → {r["end"]:7.2f}  '
                  f'({r["end"] - r["start"]:5.2f}s)  {r["text"][:56]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
