"""The formats a label can generate — the ones that ship, and the ones a tenant adds.

Every one of these used to be unrepresentable. `services/briefs` encoded exactly one kind
of asset in Python: a templatable backdrop loop, four fixed moods, `clean centre frame
left empty for a subject` in the prompt and `speech` in the negatives. That is a good
description of one product and a poor description of a video tool, and the gap showed the
first time somebody asked for their artist talking to camera — the pipeline would have
spent real money instructing a model not to produce speech.

So the formats are rows. The built-ins are what ships; a tenant copies and edits them, and
adding a format is adding a record rather than editing the planner. The five below are a
starting set and not a taxonomy — a label makes fan-facing UGC bait, and also cover art,
press stills, lyric cards, ads and announcements, and none of those is more native to this
app than the others.

Three rules hold across all of them, because they are the parts that are not opinion:

- **A backdrop must not score itself.** Every live video model returns a native audio
  track, so any format whose asset is meant to sit under a master carries the audio
  negatives. A format whose asset *is* the performance does not — that is the whole
  point of it. The negatives are the conditioning half; `score_with_master` is the
  deterministic half, and a format that gets one wrong should not get the other right.
- **The face policy has to match the framing.** A format that asks for a locked face and
  a wide empty frame is asking for two different pictures.
- **The shape of the frame is stated once.** `aspect_ratio` reaches the model as a param
  and as the `{aspect}` clause; typing "vertical 9:16" into the prose as well is how the
  field became decorative and a re-shaped format kept rendering vertical.
"""

from __future__ import annotations

import logging

from remixkit.auth.provider import Principal
from remixkit.domain.models import ApprovalState, FacePolicy, FrameAngle, Modality, Recipe
from remixkit.ports.repository import DocumentRepository
from remixkit.services.errors import Conflict, NotFound

log = logging.getLogger(__name__)

COLLECTION = "recipes"

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
                "Shot as a {aspect} loop, {variant}. "
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
                "Hyper-realistic {aspect} selfie video, shot on a phone held at arm's length. "
                "{artist} looking straight into the lens and speaking casually, {variant}. "
                # No pronoun. A format is seeded once per tenant and then used by every
                # artist on the roster, so a pronoun written into it is a guess applied to
                # people it was never asked about — and it reaches the provider, which
                # renders what it is told. The artist is already named in the clause above.
                'Speaking in a natural conversational tone: "{line}". '
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
            # The one format here whose audio is the asset. Laying the master over it
            # deletes the line the model was paid to speak — see `Recipe.score_with_master`.
            score_with_master=False,
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
                'Cinematic {aspect} performance clip for "{title}" by {artist}. '
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
                "Editorial portrait photograph of {artist}, {aspect}. "
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
        Recipe(
            tenant_id="",
            slug="voice-over",
            name="Voice-over",
            intent="One spoken line as audio, to lay over a clip.",
            modality=Modality.AUDIO,
            builtin=True,
            # The line and nothing else. A TTS provider is being asked to *say* this, not
            # to interpret a description of a scene — every extra clause is a word it may
            # read aloud.
            prompt_template="{line}",
            variants=[""],
            seconds_from="none",
            face=FacePolicy.NONE,
        ),
    ]


# What a kit is if nobody says. `backdrop-loop` was the only thing this app made, and it
# is the wrong default now that there is a choice: a backdrop deliberately has *nobody* in
# it, so defaulting to it would mean an artist's identity — the reference frames, the
# silhouette, the locked still — reaching nothing unless somebody went looking for the
# right format. Likeness is the point; the format that carries it is the default, and the
# empty-centre backdrop is a deliberate pick.
DEFAULT_SLUG = "performance"

# The format a spoken line is rendered with when a brief asks for one alongside its video.
# A kit is one format plus, optionally, this — which is the honest description of what
# `tts_text` always was, rather than a second modality bolted into the video planner.
VOICE_SLUG = "voice-over"


class RecipeService:
    """The tenant's formats, seeded from the built-ins on first read.

    Seeding lazily rather than at startup, because a tenant is created by the first
    request that mentions it and there is no migration step to hang a fixture on. The
    first `list()` writes the built-ins and every later one reads them, so a tenant always
    has somewhere to start and an operator who has edited them is never overwritten.

    Built-ins are seeded as `APPROVED`. They ship having been thought about; making a label
    approve four records before generating anything would be ceremony rather than a gate.
    """

    def __init__(self, repo: DocumentRepository) -> None:
        self._repo = repo

    def list(self, principal: Principal) -> list[Recipe]:
        stored = self._repo.list(principal.tenant_id, COLLECTION, Recipe)
        if not stored:
            stored = self._seed(principal)
        return sorted(stored, key=lambda r: (not r.builtin, r.name.lower()))

    def _seed(self, principal: Principal) -> list[Recipe]:
        made: list[Recipe] = []
        for recipe in builtin_recipes():
            recipe.tenant_id = principal.tenant_id
            recipe.approval = ApprovalState.APPROVED
            self._repo.put(principal.tenant_id, COLLECTION, recipe.id, recipe)
            made.append(recipe)
        log.info("seeded %d built-in recipes for %s", len(made), principal.tenant_id)
        return made

    def get(self, principal: Principal, slug: str) -> Recipe:
        """By slug, because that is what a kit brief stores.

        The id would be the more usual key and is the wrong one here: a brief has to name
        a *format*, and a format that gets edited is still the same format. Storing the id
        would mean a kit re-run after an edit either points at a record that no longer
        describes it or at nothing at all.
        """
        wanted = (slug or DEFAULT_SLUG).strip()
        for recipe in self.list(principal):
            if recipe.slug == wanted:
                return recipe
        raise NotFound(f"No recipe {wanted!r}")

    def save(self, principal: Principal, recipe: Recipe) -> Recipe:
        recipe.tenant_id = principal.tenant_id
        recipe.touch()
        self._repo.put(principal.tenant_id, COLLECTION, recipe.id, recipe)
        return recipe

    def delete(self, principal: Principal, slug: str) -> None:
        """A built-in refuses. It is the floor a tenant falls back to."""
        recipe = self.get(principal, slug)
        if recipe.builtin:
            raise Conflict(
                f"{recipe.name!r} ships with the app. Copy it and edit the copy — "
                "deleting it would leave a tenant with no format to start from."
            )
        self._repo.delete(principal.tenant_id, COLLECTION, recipe.id)
