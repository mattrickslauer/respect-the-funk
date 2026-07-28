"""Turning a song + an identity into a list of shots.

This is where the product opinion lives, and it is worth being explicit about what it
is *not*: it is not a recommender for which archetype goes viral. Research calls that
folklore, and BUILD-SPEC §7 lists it under "what we deliberately do NOT build". The
moods below are a spread for variety, not a prediction.

What the plan does encode is the one supported lever — **templatability**. Every shot
is 9:16, cut to a hook window, and shaped so a fan can obviously make their own version.
The song is the substrate; the template is the product.

A song has as many hooks as it has, so a kit may be cut to several. When a brief names
sections, the loops are dealt round-robin across them: every named hook gets a loop before
any hook gets a second one, and each loop's length is *its own* window's length. Dealing
rather than multiplying is deliberate — `video_count` stays the number of videos bought,
so adding a second hook to a brief does not double the invoice without anybody asking.
"""

from __future__ import annotations

from remixkit.domain.models import HookWindow, Identity, Modality, Song
from remixkit.ports.generator import ShotSpec

# A spread, not a ranking. Named so the UI can show what it is about to buy.
MOODS: list[tuple[str, str]] = [
    ("night-drive", "moody night drive, city lights streaking past, shallow depth of field"),
    ("warm-interior", "warm interior, golden practical lighting, intimate handheld framing"),
    ("hard-flash", "high-contrast direct flash, editorial, stark shadows"),
    ("open-air", "open air at golden hour, wind, natural motion"),
]

# The renderer's clamp on a loop, in seconds. `services.recommendations` pins its own
# constants against these so the length a recommendation proposes and the length a kit
# actually renders cannot drift apart.
MIN_LOOP_S = 3.0
MAX_LOOP_S = 10.0
DEFAULT_LOOP_S = 6.0


def hook_windows(song: Song, section_ids: list[str] | None = None) -> list[tuple[str, HookWindow]]:
    """The (name, window) pairs a kit will cut to, in song order.

    Named sections when the brief names them, otherwise the primary hook window — which is
    what every kit cut before sections existed used, and what a song with one hook still
    means. A named section that has since been deleted is dropped rather than substituted:
    a kit that quietly renders a different part of the song than its brief says is worse
    than a kit that renders one fewer loop.
    """
    if section_ids:
        wanted = set(section_ids)
        chosen = [s for s in song.ordered_sections if s.id in wanted and s.duration_ms > 0]
        if chosen:
            return [(s.display_name, s.window) for s in chosen]
    if song.hook.duration_ms:
        primary = song.primary_section
        return [(primary.display_name if primary else "hook", song.hook)]
    return []


def _loop_seconds(window: HookWindow) -> float:
    """Loop length follows the hook window when it has been measured.

    A loop that is not the length of the hook is a loop a fan cannot cut to. An unmeasured
    song falls back to six seconds, which is a guess, and every screen that shows it says
    so rather than printing it like a measurement.
    """
    if not window.duration_ms:
        return DEFAULT_LOOP_S
    return max(MIN_LOOP_S, min(MAX_LOOP_S, round(window.duration_ms / 1000, 1)))


def default_shot_plan(
    song: Song,
    identity: Identity | None,
    *,
    video_count: int = 3,
    hook_lines: list[str] | None = None,
    tts_text: str | None = None,
    max_shots: int = 8,
    section_ids: list[str] | None = None,
) -> list[ShotSpec]:
    """The default kit: a few vertical loops, a lyric card per hook line, optional TTS."""
    shots: list[ShotSpec] = []
    windows = hook_windows(song, section_ids) or [("hook", song.hook)]

    # With one window there is nothing to tell a fifth loop from the first, so the mood
    # spread is still the ceiling. With several, the same mood over a different hook is a
    # genuinely different shot, and the ceiling rises with them.
    slots = max(0, min(video_count, len(MOODS) * len(windows)))
    for index in range(slots):
        name, description = MOODS[index % len(MOODS)]
        section_name, window = windows[index % len(windows)]
        shots.append(
            ShotSpec(
                modality=Modality.VIDEO,
                prompt=f"Vertical 9:16 loop, {description}. Templatable backdrop for a fan video, "
                f"clean centre frame left empty for a subject.",
                seconds=_loop_seconds(window),
                aspect_ratio="9:16",
                label=f"{name} · {section_name}",
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
                label="lyric card",
            )
        )

    if tts_text and tts_text.strip():
        shots.append(ShotSpec(modality=Modality.AUDIO, prompt=tts_text.strip(), label="voice"))

    # Capped rather than truncated silently — the caller is told in the kit brief.
    return shots[:max_shots]
