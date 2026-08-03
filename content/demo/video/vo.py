"""Render the voice-over, and keep the alignment that comes back with it.

The words are not authored here. They are read out of `docs/demo-voiceover.txt`,
sliced from the line the on-camera intro hands over at, so there is exactly one copy
of the script in the repo and this file cannot drift from it.

The reason this calls `/with-timestamps` rather than plain text-to-speech is the whole
design of the edit. One request returns the audio *and* a character-level alignment, so
every visual cue downstream is anchored to the frame where a particular word is actually
spoken — "performance clip" lights the performance card because the narrator said it,
not because someone guessed at 4.2 seconds. Splitting the script into per-line requests
would have given the same timing and cost the same characters, but each line would have
been synthesised without knowing the sentence before it, and the prosody shows it.

The account is on the free tier: 10,000 characters, total, ever. This script refuses to
spend any of them if the output it would produce is already on disk, which is why
`--force` exists and why it prints the remaining balance every run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCRIPT = REPO / "docs" / "demo-voiceover.txt"
OUT = HERE / "out"

# The intro is delivered on camera, live. The generated read starts here.
HANDOVER = "Register an artist"

# Eric — male, American, even. Chosen to sit *under* the on-camera intro rather than
# match it: this half of the video is the system talking, not the founder.
VOICE_ID = "cjVigY5qzO86Huf0OWal"
MODEL_ID = "eleven_multilingual_v2"

API = "https://api.elevenlabs.io/v1"


def env_roots() -> list[Path]:
    """Where a `.env` might be — this checkout, and the main one if this is a worktree.

    `.env` is gitignored, so a `git worktree` checkout never has one. Falling back to
    the main working tree means this runs from an agent's worktree without asking
    anybody to copy a secret around.
    """
    roots = [REPO]
    try:
        import subprocess

        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if common:
            roots.append(Path(common).parent)
    except Exception:
        pass
    return roots


def api_key() -> str:
    """Read the key from the repo-root .env, the one place it is already kept."""
    if key := os.environ.get("ELEVEN_LABS_API"):
        return key
    for root in env_roots():
        env = root / ".env"
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ELEVEN_LABS_API="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("no ELEVEN_LABS_API in the environment or in .env")


def script_text() -> str:
    """The generated half of the voice-over, verbatim."""
    full = SCRIPT.read_text(encoding="utf-8")
    i = full.find(HANDOVER)
    if i < 0:
        sys.exit(f"{SCRIPT} no longer contains {HANDOVER!r} — the handover line moved")
    return full[i:].strip()


def request(path: str, key: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}/{path}",
        data=data,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"elevenlabs {e.code} on {path}: {e.read().decode()[:500]}")


def balance(key: str) -> tuple[int, int]:
    sub = request("user/subscription", key)
    return sub["character_count"], sub["character_limit"]


def word_times(alignment: dict) -> list[dict]:
    """Collapse the character alignment into words.

    The API returns one start/end per character, including the spaces. A cue wants to
    fire on a word, so characters are accumulated until whitespace closes the run. The
    word keeps its punctuation — matching downstream is done on a normalised form, and
    keeping the raw text makes the dump readable when a cue does not fire.
    """
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]

    words, buf, first = [], "", None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"word": buf, "start": first, "end": last})
                buf = ""
            continue
        if not buf:
            first = s
        buf, last = buf + ch, e
    if buf:
        words.append({"word": buf, "start": first, "end": last})
    return words


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-synthesise and spend characters again")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    mp3, align = OUT / "narration.mp3", OUT / "alignment.json"

    text = script_text()
    key = api_key()

    if mp3.exists() and align.exists() and not args.force:
        have = json.loads(align.read_text())
        if have.get("text") == text:
            print(f"narration is current ({have['duration']:.1f}s) — nothing to spend")
            return
        print("the script changed since the last read — re-synthesising")

    used, limit = balance(key)
    print(f"quota: {used}/{limit} used, {limit - used} left · this read costs {len(text)}")
    if len(text) > limit - used:
        sys.exit("not enough characters left on the account for a full read")

    got = request(
        f"text-to-speech/{VOICE_ID}/with-timestamps",
        key,
        {
            "text": text,
            "model_id": MODEL_ID,
            # Stability high enough that the long measured sentences do not wander,
            # style low because this is narration and not a performance.
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0},
        },
    )

    mp3.write_bytes(base64.b64decode(got["audio_base64"]))
    words = word_times(got["alignment"])
    duration = words[-1]["end"] if words else 0.0

    align.write_text(
        json.dumps(
            {
                "voice_id": VOICE_ID,
                "model_id": MODEL_ID,
                "duration": duration,
                "text": text,
                "words": words,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    now, _ = balance(key)
    print(f"wrote {mp3.name} — {duration:.1f}s, {len(words)} words")
    print(f"quota now {now}/{limit}, {limit - now} left")


if __name__ == "__main__":
    main()
