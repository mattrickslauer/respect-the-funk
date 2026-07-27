"""Turning a song + an identity into a list of shots.

This is where the product opinion lives, and it is worth being explicit about what it
is *not*: it is not a recommender for which archetype goes viral. Research calls that
folklore, and BUILD-SPEC §7 lists it under "what we deliberately do NOT build". The
moods below are a spread for variety, not a prediction.

What the plan does encode is the one supported lever — **templatability**. Every shot
is 9:16, cut to the hook window, and shaped so a fan can obviously make their own
version. The song is the substrate; the template is the product.
"""

from __future__ import annotations

from remixkit.domain.models import Identity, Modality, Song
from remixkit.ports.generator import ShotSpec

# A spread, not a ranking. Named so the UI can show what it is about to buy.
MOODS: list[tuple[str, str]] = [
    ("night-drive", "moody night drive, city lights streaking past, shallow depth of field"),
    ("warm-interior", "warm interior, golden practical lighting, intimate handheld framing"),
    ("hard-flash", "high-contrast direct flash, editorial, stark shadows"),
    ("open-air", "open air at golden hour, wind, natural motion"),
]


def default_shot_plan(
    song: Song,
    identity: Identity | None,
    *,
    video_count: int = 3,
    hook_lines: list[str] | None = None,
    tts_text: str | None = None,
    max_shots: int = 8,
) -> list[ShotSpec]:
    """The default kit: a few vertical loops, a lyric card per hook line, optional TTS."""
    shots: list[ShotSpec] = []

    # Loop length follows the hook window when it has been measured. A loop that is
    # not the length of the hook is a loop a fan cannot cut to.
    hook_seconds = song.hook.duration_ms / 1000 if song.hook.duration_ms else 6.0
    seconds = max(3.0, min(10.0, round(hook_seconds, 1)))

    for name, description in MOODS[: max(0, video_count)]:
        shots.append(
            ShotSpec(
                modality=Modality.VIDEO,
                prompt=f"Vertical 9:16 loop, {description}. Templatable backdrop for a fan video, "
                f"clean centre frame left empty for a subject.",
                seconds=seconds,
                aspect_ratio="9:16",
            )
        )
        shots[-1].prompt += f" [{name}]"

    for line in hook_lines or []:
        line = line.strip()
        if not line:
            continue
        shots.append(
            ShotSpec(
                modality=Modality.IMAGE,
                prompt=f'Bold vertical lyric card, high contrast, large type, text: "{line}"',
                aspect_ratio="9:16",
            )
        )

    if tts_text and tts_text.strip():
        shots.append(ShotSpec(modality=Modality.AUDIO, prompt=tts_text.strip()))

    # Capped rather than truncated silently — the caller is told in the kit brief.
    return shots[:max_shots]
