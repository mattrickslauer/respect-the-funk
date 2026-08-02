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

from remixkit.domain.models import (
    FacePolicy,
    HookWindow,
    Identity,
    Modality,
    Recipe,
    Song,
)
from remixkit.ports.generator import ShotSpec, position_key

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

# What a backdrop loop must not contain.
#
# The first four exist because every live video model generates a *soundtrack* by default
# — Veo, Sora 2, and Seedance all return video with a native audio track. On 2026-07-31 a
# kit for "Un Poquito Más" came back scored with a lofi instrumental the model wrote
# itself, over a song it had never heard. A kit loop is a backdrop the master plays over;
# audio in it is not a bonus, it is a defect.
#
# These are best-effort and it is worth saying so plainly: `negative_prompt` is a
# *conditioning* signal, not a mute switch, and no provider here documents it as binding
# on the audio track. The deterministic fix is to drop the track on delivery — see
# `README` under "Silent loops". This raises the odds; it does not settle them.
#
# The last four are the templatability rule as a constraint rather than a hope: a backdrop
# with a caption burned into it is a backdrop a fan cannot put their own words on.
VIDEO_NEGATIVES: list[str] = [
    "music",
    "soundtrack",
    "singing",
    "speech",
    "on-screen text",
    "captions",
    "subtitles",
    "watermark",
]


def _tempo_phrase(song: Song) -> str:
    """How fast the picture should move, from the tempo that was actually measured.

    Nothing is invented here. An unmeasured song contributes no phrase at all rather than
    a plausible one — the same rule the rest of the model follows about numbers whose
    origin is not recorded. A prompt that says "112 BPM" about a song nobody measured is a
    guess wearing a measurement's clothes.
    """
    if not song.bpm:
        return ""
    if song.bpm < 90:
        return f"slow, unhurried camera movement at {song.bpm:.0f} BPM"
    if song.bpm < 125:
        return f"steady mid-tempo movement at {song.bpm:.0f} BPM"
    return f"quick, driving movement at {song.bpm:.0f} BPM"


def _song_phrase(song: Song, artist_name: str | None) -> str:
    """Which record this is for, in the words a model can use.

    Naming the title does little on its own — no video model has heard this song. It is
    here because the prompt is *editable*, and a starting point that names the release is
    one an operator can steer; a starting point that names nothing reads as a stock clip
    and gets bought as one.
    """
    who = f" by {artist_name}" if artist_name else ""
    return f'Backdrop for the music release "{song.title}"{who}'


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


def plan_from_recipe(
    recipe: Recipe,
    song: Song,
    *,
    count: int = 3,
    max_shots: int = 8,
    section_ids: list[str] | None = None,
    artist_name: str | None = None,
    line: str = "",
) -> list[ShotSpec]:
    """Shots for one format. The planner, with the product opinion taken out of it.

    Everything that used to be a constant here — the mood spread, the framing clause, what
    the shot must not contain, how long it runs, how much of the artist it needs — now
    comes off the recipe. What is left is the part that is genuinely arithmetic: deal the
    requested count across the chosen hooks so every named window gets a shot before any
    window gets a second one, and cycle the variants for spread.

    `line` is what a spoken format says. It is a parameter rather than a template field
    baked into the recipe because the recipe is the *format* and the line is the content —
    the same "direct address" recipe announces a show this month and thanks a city next
    month.
    """
    windows = hook_windows(song, section_ids) or [("hook", song.hook)]
    tempo = _tempo_phrase(song)
    shots: list[ShotSpec] = []

    variants = recipe.variants or [""]
    # With one window there is nothing to tell a fifth asset from the first, so the variant
    # spread is the ceiling. With several, the same variant over a different hook is a
    # genuinely different shot and the ceiling rises with them.
    slots = max(0, min(count, len(variants) * len(windows)))

    for index in range(slots):
        variant = variants[index % len(variants)]
        section_name, window = windows[index % len(windows)]

        seconds: float | None = None
        if recipe.seconds_from == "hook":
            seconds = _loop_seconds(window)
        elif recipe.seconds_from == "fixed":
            seconds = recipe.fixed_seconds

        shots.append(
            ShotSpec(
                modality=recipe.modality,
                prompt=recipe.render(
                    artist=artist_name or "",
                    title=song.title,
                    tempo=tempo,
                    section=section_name,
                    variant=variant,
                    line=line,
                ),
                seconds=seconds,
                aspect_ratio=recipe.aspect_ratio,
                label=f"{recipe.name} · {section_name}"
                if recipe.seconds_from == "hook"
                else recipe.name,
                negatives=list(recipe.negatives),
                use_identity_plate=recipe.face in (FacePolicy.PLATE, FacePolicy.LOCKED),
                identity_lock=recipe.face is FacePolicy.LOCKED,
                # The class this framing actually wants. `None` leaves the global ranking
                # in charge, which is right for a format with no opinion and wrong for a
                # selfie — a phone at arm's length is front-on, and ranking three-quarter
                # first everywhere is what made it ask for the wrong reference.
                face_angle=recipe.face_angle,
                # A format that wants no identity at all does not get the text either.
                # `NONE` is not "no picture of them", it is "this asset is not about them".
                suppress_identity_text=recipe.face is FacePolicy.NONE,
                recipe_slug=recipe.slug,
                # Which hook this shot was dealt, kept as numbers rather than only as a
                # name in the label. The delivered asset carries the master's own audio,
                # and this is the answer to "from where" — per shot, because the deal is
                # round-robin and the third shot of a two-hook brief is the first hook
                # again while the second one is not.
                hook_start_ms=window.start_ms,
                hook_end_ms=window.end_ms,
            )
        )

    return shots[:max_shots]


def default_shot_plan(
    song: Song,
    identity: Identity | None,
    *,
    video_count: int = 3,
    tts_text: str | None = None,
    max_shots: int = 8,
    section_ids: list[str] | None = None,
    artist_name: str | None = None,
) -> list[ShotSpec]:
    """The default kit: a few vertical loops cut to the hooks, and optional TTS.

    "Default" is the operative word. Every prompt this builds is a *starting point* the
    generate screen shows in full and lets a person rewrite before anything is bought.
    That is the correction to how these prompts used to work: the plan was four fixed mood
    strings, identical for every artist and every song in the catalogue, and the screen
    showed only the mood fragment — so the thing on the page was never the thing on the
    wire, and nobody could see that the song was not mentioned in it.
    """
    shots: list[ShotSpec] = []
    windows = hook_windows(song, section_ids) or [("hook", song.hook)]
    subject = _song_phrase(song, artist_name)
    tempo = _tempo_phrase(song)

    # With one window there is nothing to tell a fifth loop from the first, so the mood
    # spread is still the ceiling. With several, the same mood over a different hook is a
    # genuinely different shot, and the ceiling rises with them.
    slots = max(0, min(video_count, len(MOODS) * len(windows)))
    for index in range(slots):
        name, description = MOODS[index % len(MOODS)]
        section_name, window = windows[index % len(windows)]
        clauses = [
            subject,
            f"vertical 9:16 loop, {description}",
            tempo,
            "templatable backdrop for a fan video, clean centre frame left empty for a subject",
            # Stated in the prompt as well as the negatives because the two reach different
            # models differently, and the failure this prevents is expensive.
            "silent footage — no music, no audio track, no on-screen text",
        ]
        shots.append(
            ShotSpec(
                modality=Modality.VIDEO,
                prompt=". ".join(c for c in clauses if c) + f". [{name} · {section_name}]",
                seconds=_loop_seconds(window),
                aspect_ratio="9:16",
                label=f"{name} · {section_name}",
                negatives=list(VIDEO_NEGATIVES),
                # The loops are what a fan puts themselves into, so the artist is the one
                # thing in them that must not drift.
                use_identity_plate=True,
                # Settle the face in a still, then render the clip from it. A video model
                # conditioned on text drifts across the clip; one conditioned on a first
                # frame is anchored to it — so the likeness is decided once, cheaply, and
                # inherited rather than re-derived four times at video prices.
                #
                # Costs an image per loop and degrades to a plain plate-conditioned clip
                # when there is no image provider or no reference frame to lock from.
                identity_lock=True,
                # The seconds of the record this loop is a backdrop for. See
                # `plan_from_recipe` — same reason, same per-shot deal.
                hook_start_ms=window.start_ms,
                hook_end_ms=window.end_ms,
            )
        )

    if tts_text and tts_text.strip():
        shots.append(ShotSpec(modality=Modality.AUDIO, prompt=tts_text.strip(), label="voice"))

    # Capped rather than truncated silently — the caller is told in the kit brief.
    return shots[:max_shots]


# What a person is allowed to change about a shot before buying it. Deliberately short:
# these are the fields that alter the output or the price, and every one of them is
# already understood end-to-end by `ports.generator`. Aspect ratio is absent because the
# whole product is 9:16 (BUILD-SPEC §7); modality is absent because changing it would
# change which provider runs and make the label meaningless.
OVERRIDABLE = ("prompt", "model", "seconds", "identity_lock")

# Not an override — the shape of the shot an override was written against, carried
# alongside it so a position that has since come to mean something else can be told apart
# from one that still means what it did. See `ports.generator.position_key`. Deliberately
# outside `OVERRIDABLE`, so it neither reaches a `ShotSpec` field nor gets persisted into
# the brief: by the time a kit is queued, the plan's shape is fixed by that brief and the
# worker re-plans the same list from it.
POSITION_KEY = "position_key"

# Fields whose value is a yes/no rather than a string. They need their own coercion
# because a checkbox that is simply *absent* when unticked cannot express "off" — absence
# already means "use the default", and for a field that defaults to on those are opposite
# intentions. So the form sends the answer explicitly and this reads it.
BOOLEAN_OVERRIDES = frozenset({"identity_lock"})
_TRUE = frozenset({"on", "1", "true", "yes"})
_FALSE = frozenset({"off", "0", "false", "no"})


def overrides_from_brief(brief: dict) -> dict[int, dict]:
    """Read the stored override list back into the map `apply_overrides` takes.

    The round trip exists because `Kit.brief` is persisted as JSON and JSON has no integer
    keys: a map written as `{0: {...}}` returns as `{"0": {...}}` and matches no shot, so
    the edits vanish between the request and the worker with nothing logged.
    """
    out: dict[int, dict] = {}
    for entry in brief.get("shot_overrides") or []:
        try:
            index = int(entry["index"])
        except (KeyError, TypeError, ValueError):
            continue
        out[index] = {k: v for k, v in entry.items() if k in OVERRIDABLE}
    return out


def apply_overrides(
    shots: list[ShotSpec], overrides: dict[int, dict] | None
) -> list[ShotSpec]:
    """Replace per-shot fields a person edited, by position in the plan.

    Position is the key because the plan is a pure function of the brief: the same
    `video_count`, hook lines, and section ids always deal the same shots in the same
    order, so index N means the same thing to the screen that showed it and to the worker
    that runs it. Anything that changes the shape of the plan — a different video count, a
    section ticked — necessarily invalidates the positions, which is why the console
    re-prices on every such change and carries the overrides it re-priced with.

    An override for a position that no longer exists is dropped rather than applied to a
    neighbour. Silently moving somebody's rewritten prompt onto a different hook is worse
    than losing it, because losing it is visible on the screen that shows every prompt.

    That rule was only half enforced: a position can also survive while ceasing to mean the
    same thing. Switching format re-deals the list, and an edit made on index 1 of a
    two-clip performance brief was applied to index 1 of a direct-address brief — the
    voice-over — so a TTS provider was handed a video prompt to read aloud. An override that
    arrives with a `position_key` is now checked against the shot standing there, and
    dropped on a mismatch for the same reason a vanished index is.

    An empty or whitespace-only value is not an override — it is a field somebody cleared,
    and the default belongs back in it.
    """
    if not overrides:
        return shots

    for index, edits in overrides.items():
        if not 0 <= index < len(shots):
            continue
        shot = shots[index]
        expected = edits.get(POSITION_KEY)
        if expected and expected != position_key(shot.modality, shot.recipe_slug):
            continue
        for field_name in OVERRIDABLE:
            if field_name not in edits:
                continue
            value = edits[field_name]
            if isinstance(value, str):
                value = value.strip()
            if value in (None, ""):
                continue
            if field_name in BOOLEAN_OVERRIDES:
                if isinstance(value, bool):
                    setattr(shot, field_name, value)
                    continue
                lowered = str(value).strip().lower()
                if lowered in _TRUE:
                    setattr(shot, field_name, True)
                elif lowered in _FALSE:
                    setattr(shot, field_name, False)
                # Anything else is not an answer, so the default stands.
                continue
            if field_name == "seconds":
                try:
                    value = max(MIN_LOOP_S, min(MAX_LOOP_S, float(value)))
                except (TypeError, ValueError):
                    continue
            setattr(shot, field_name, value)
            if field_name == "prompt":
                # What a person typed is the finished string, not a fragment to prepend the
                # identity to — the box they typed it in was showing the composed prompt.
                shot.prompt_verbatim = True
    return shots
