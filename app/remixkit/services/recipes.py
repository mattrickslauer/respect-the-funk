"""The formats a label can generate — the ones that ship, and the ones a tenant adds.

Every one of these used to be unrepresentable. `services/briefs` encoded exactly one kind
of asset in Python: a templatable backdrop loop, four fixed moods, `clean centre frame
left empty for a subject` in the prompt and `speech` in the negatives. That is a good
description of one product and a poor description of a video tool, and the gap showed the
first time somebody asked for their artist talking to camera — the pipeline would have
spent real money instructing a model not to produce speech.

So the formats are rows. `BUILTIN` is what ships; a tenant copies and edits them, and
adding a format is adding a record rather than editing the planner.

Two rules hold across all of them, because they are the parts that are not opinion:

- **A backdrop must not score itself.** Every live video model returns a native audio
  track, so any format whose asset is meant to sit under a master carries the audio
  negatives. A format whose asset *is* the performance does not — that is the whole
  point of it.
- **The face policy has to match the framing.** A format that asks for a locked face and
  a wide empty frame is asking for two different pictures.
"""

from __future__ import annotations

from remixkit.domain.models import FacePolicy, FrameAngle, Modality, Recipe

# What a shot that sits *under* the master must not contain. Audio first: Veo, Sora 2 and
# Seedance all return a soundtrack unless told otherwise, and on 2026-07-31 a kit came back
# scored with a lofi instrumental the model wrote over a song it had never heard.
SILENT_NEGATIVES = [
    "music",
    "soundtrack",
    "singing",
    "speech",
    "on-screen text",
    "captions",
    "subtitles",
    "watermark",
]

# What a shot whose asset *is* someone speaking must not contain. Deliberately short, and
# deliberately missing every audio term above — suppressing speech in a talking clip is
# paying a provider to defeat the brief.
# A performance clip still sits under the master, so the model's own *audio* is a defect —
# but "singing" is the thing being filmed. Negativing it suppresses the picture the format
# exists to make, which is the same class of mistake as negativing speech in a talking clip.
PERFORMING_NEGATIVES = [
    "music",
    "soundtrack",
    "speech",
    "on-screen text",
    "captions",
    "subtitles",
    "watermark",
]

SPOKEN_NEGATIVES = [
    "on-screen text",
    "captions",
    "subtitles",
    "watermark",
    "distorted face",
    "extra fingers",
]


def builtin_recipes() -> list[Recipe]:
    """The formats that ship. `tenant_id` is filled in when they are seeded."""
    return [
        Recipe(
            tenant_id="",
            slug="backdrop-loop",
            name="Backdrop loop",
            intent="A silent vertical loop a fan can put themselves in front of.",
            modality=Modality.VIDEO,
            builtin=True,
            prompt_template=(
                'Backdrop for the music release "{title}" by {artist}. '
                "Vertical 9:16 loop, {variant}. "
                "{tempo}. "
                "Templatable backdrop for a fan video, clean centre frame left empty for a subject. "
                "Silent footage — no music, no audio track, no on-screen text"
            ),
            negatives=list(SILENT_NEGATIVES),
            variants=[
                "moody night drive, city lights streaking past, shallow depth of field",
                "warm interior, golden practical lighting, intimate handheld framing",
                "high-contrast direct flash, editorial, stark shadows",
                "open air at golden hour, wind, natural motion",
            ],
            seconds_from="hook",
            # Nobody in it, by definition — the empty centre is the product.
            face=FacePolicy.NONE,
        ),
        Recipe(
            tenant_id="",
            slug="direct-address",
            name="Direct address",
            intent="The artist talking straight to camera, phone-in-hand. Announcements, invites, thanks.",
            modality=Modality.VIDEO,
            builtin=True,
            prompt_template=(
                "Hyper-realistic vertical selfie video, shot on a phone held at arm's length. "
                "{artist} looking straight into the lens and speaking casually, {variant}. "
                'She says, in a natural conversational tone: "{line}". '
                "Available light, slight handheld motion, candid and unposed, no camera moves"
            ),
            negatives=list(SPOKEN_NEGATIVES),
            variants=[
                "in a sunlit domestic kitchen, morning light",
                "in a kitchen leaning against the counter, warm evening light",
            ],
            # A sentence takes as long as it takes. Clipping speech to a musical bar cuts
            # a word in half, which is worse than ignoring the grid entirely.
            seconds_from="fixed",
            fixed_seconds=8.0,
            face=FacePolicy.LOCKED,
            # Front, not three-quarter. A phone at arm's length is a front-on framing, and
            # ranking the reference set globally is what made a selfie ask for the wrong
            # class — the format knows what it needs and the global order is only a
            # fallback for formats that do not care.
            face_angle=FrameAngle.FRONT,
        ),
        Recipe(
            tenant_id="",
            slug="performance",
            name="Performance clip",
            intent="The artist performing to camera — cinematic rather than casual.",
            modality=Modality.VIDEO,
            builtin=True,
            prompt_template=(
                'Cinematic vertical performance clip for "{title}" by {artist}. '
                "{artist} performing to camera, {variant}. "
                "{tempo}. "
                "Shallow depth of field, filmic grade, subject fills the frame"
            ),
            negatives=list(PERFORMING_NEGATIVES),
            variants=[
                "close on the face, single hard key light against darkness",
                "medium shot, practical neon behind, slow push in",
                "wide, empty stage, single overhead spot",
            ],
            seconds_from="hook",
            face=FacePolicy.LOCKED,
            # Three-quarter earns it here: a cinematic frame is rarely flat-on, and the
            # class carries both the front planes and the depth.
            face_angle=FrameAngle.THREE_QUARTER_LEFT,
        ),
        Recipe(
            tenant_id="",
            slug="portrait-still",
            name="Portrait still",
            intent="A single press or cover frame of the artist. Cheap, and reusable as a reference.",
            modality=Modality.IMAGE,
            builtin=True,
            prompt_template=(
                "Editorial portrait photograph of {artist}, vertical 9:16. "
                "{variant}. "
                "Sharp focus on the face, natural skin texture, no motion blur"
            ),
            negatives=["watermark", "signature", "extra fingers", "distorted face", "text"],
            variants=[
                "neutral studio backdrop, soft key light",
                "on location at golden hour, backlit",
            ],
            seconds_from="none",
            face=FacePolicy.PLATE,
            face_angle=FrameAngle.FRONT,
        ),
    ]
