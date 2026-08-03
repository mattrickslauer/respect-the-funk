"""The document model.

Five entities, and the vocabulary is PRODUCT.md's — `tenant`, `artist`, `identity`,
`song`, `kit`. Nothing here does I/O or knows what a bucket is; that is the whole
point of keeping it separate. Persisting these is a `ports.repository` concern,
generating from them is a `ports.generator` concern.

`tenant_id` is on every model, per BUILD-SPEC §2b rule 6 — a partition key that is
"cheap on day one and near-impossible to retrofit". It is populated today from the
anonymous dev principal; when auth lands it comes off the real one and no model,
service, or storage key changes shape.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(value: str) -> str:
    """Filesystem- and object-key-safe slug. Artists get one; it is their stable handle."""
    norm = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return slug or "untitled"


class ApprovalState(str, Enum):
    """PRODUCT.md gap #2: nothing in the repo carried editorial state.

    This is deliberately *not* the same axis as `render_edit --check`, which is a
    technical gate (beats tile, cuts land on the grid). A label cannot auto-publish
    AI content about its own artists, so a human moves this.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"


class KitStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class Modality(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class SectionRole(str, Enum):
    """What a stretch of the song is, in the vocabulary a label already speaks.

    Three of these — `drop`, `chorus`, `breakdown` — are also what the analyser emits,
    and it is worth being exact about what that emission is: an **energy tier**, not a
    lyric- or structure-aware classification. `adapters.audio_numpy` measures kick energy
    per bar and names the tiers; it does not know a chorus from a post-chorus and does not
    claim to. The label is a suggestion a person may rename in one click; the numbers
    under it — start, end, bars, low-band energy — are measured and carry their method.
    """

    INTRO = "intro"
    VERSE = "verse"
    PRE_CHORUS = "pre-chorus"
    CHORUS = "chorus"
    HOOK = "hook"
    DROP = "drop"
    BRIDGE = "bridge"
    BREAKDOWN = "breakdown"
    OUTRO = "outro"

    @property
    def is_hook_material(self) -> bool:
        """Which roles a kit may cut a loop to.

        A verse is a legitimate thing to mark and a poor thing to loop, so the roles are
        split rather than the list being filtered by hand at every call site.
        """
        return self in (SectionRole.HOOK, SectionRole.CHORUS, SectionRole.DROP)


class FrameAngle(str, Enum):
    """Which way the artist is facing in a reference still.

    The classes exist because a reference set is not a pile of photographs — it is a
    *cover* of the subject, and the thing that makes it a cover is that the angles are
    named. Twelve flattering front-on portraits document one view twelve times; one still
    per class documents a head.

    Ordered by how well each serves as the single conditioning image a video shot gets.
    `THREE_QUARTER_*` sits above `FRONT` deliberately: a three-quarter view carries both
    the front planes and the depth of the nose and cheekbone, which is why it is the
    standard reference angle in casting and character art. A flat front-on frame is
    unambiguous about symmetry and says almost nothing about profile.
    """

    THREE_QUARTER_LEFT = "three-quarter-left"
    THREE_QUARTER_RIGHT = "three-quarter-right"
    FRONT = "front"
    PROFILE_LEFT = "profile-left"
    PROFILE_RIGHT = "profile-right"
    BACK = "back"

    @property
    def label(self) -> str:
        return self.value.replace("-", " ")

    @classmethod
    def by_reference_quality(cls) -> list[FrameAngle]:
        """Declaration order *is* the preference order — see the class docstring."""
        return list(cls)


class Presentation(str, Enum):
    """How the artist reads on screen.

    Presentation rather than gender, and the distinction is doing real work rather than
    being decorous. What conditions a render is silhouette, styling, and how a face is
    lit and framed — none of which is recoverable from a gender field, and all of which a
    person can state directly. It is also the honest thing to store about a roster: this
    is a record of how somebody presents in their own work, which they are the authority
    on, not a demographic fact the label is asserting about them.

    `UNSPECIFIED` is the default and contributes nothing to a prompt. An artist who has
    not said should not have a guess sent to a provider on their behalf.
    """

    UNSPECIFIED = "unspecified"
    FEMININE = "feminine"
    MASCULINE = "masculine"
    ANDROGYNOUS = "androgynous"


class BodyBuild(str, Enum):
    """A standardised silhouette, in the plainest words that carry it.

    Five bands on one axis — the classical ectomorph → endomorph range — named in ordinary
    language rather than in somatotype vocabulary. That is a generation decision, not a
    stylistic one: image and video models are trained on captions written by people, and
    "athletic" appears in millions of them where "mesomorph" appears in almost none.

    Deliberately one axis and five stops. A silhouette is the highest-signal, lowest-cost
    thing an identity can tell a model — it survives compression, it reads at any framing,
    and it is the first thing that goes wrong when a prompt is vague. Splitting it into
    shoulders, waist and limb length would spend the entire prompt budget on a body that
    is, in these kits, usually out of frame.
    """

    UNSPECIFIED = "unspecified"
    SLIGHT = "slight"
    LEAN = "lean"
    ATHLETIC = "athletic"
    SOLID = "solid"
    FULL = "full"


class HeightBand(str, Enum):
    """Short / average / tall, and not a number.

    A centimetre figure is more precise and less useful: no video model has a scale, and
    "165cm" in a prompt is three tokens that condition nothing. A band conditions framing
    and proportion, which is what the number was standing in for.
    """

    UNSPECIFIED = "unspecified"
    SHORT = "short"
    AVERAGE = "average"
    TALL = "tall"


class FacePolicy(str, Enum):
    """How much of an artist a given kind of asset needs.

    Four answers, because "does this shot want the face" was previously two booleans on
    `ShotSpec` that could disagree, and because the right answer genuinely differs per
    kind of asset. A backdrop wants nobody in it. A performance clip wants the artist
    unmistakably. A lyric card wants neither the face nor the description of one.
    """

    NONE = "none"            # no identity at all — not even the text
    TEXT = "text"            # the compiled description, no reference image
    PLATE = "plate"          # description + a reference image on the shot itself
    LOCKED = "locked"        # description + a still rendered first, clip generated from it


class Provenance(str, Enum):
    """Where a number came from. There is no third option, and that is the point.

    Every measured quantity in this model is stored next to one of these plus a method
    string. `MEASURED` without a method is refused in `services.songs`, for the same
    reason a BPM is: a number whose origin is not recorded is a number nobody can check.
    """

    MANUAL = "manual"      # a person typed it
    MEASURED = "measured"  # a tool produced it, and `method` says which and how


class AnalysisStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Base(BaseModel):
    """Everything is a tenant-scoped document with a creation stamp."""

    tenant_id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()


class LikenessConsent(BaseModel):
    """PRODUCT.md gap #3 — inverted for this use case, on purpose.

    `nate-test` kept the speaker's face out of all 22 frames because source rights
    were unknown. A label generating content about its *own signed artists* wants the
    opposite: explicit, auditable likeness rights so the artist's face **can** be held
    invariant across a kit. `character.yaml`'s `consent.people_release` is the right
    hook — this lifts it to artist level, which is where it belongs.
    """

    granted: bool = False
    signed_by: str | None = None
    signed_at: datetime | None = None
    document_key: str | None = None  # object key of the signed release, if uploaded
    notes: str | None = None

    @property
    def blocks_generation(self) -> bool:
        """A kit that holds the artist's face invariant needs this true. Enforced in
        services.kits, not by convention — BUILD-SPEC §4's rights rule applied to people."""
        return not self.granted


class Recipe(Base):
    """A kind of asset the label makes — as data rather than as code.

    This is the correction to a real limit. Everything this app could generate was one
    hardcoded opinion in `services/briefs`: a templatable backdrop loop, from a fixed
    table of four moods, with `clean centre frame left empty for a subject` welded into
    the prompt and `speech` in the negatives. Perfectly good for the one thing it was
    written for, and it meant a label that wanted their artist *talking to camera* was
    paying a model to suppress the thing they asked for. The shape of the asset was not a
    setting; it was a commit.

    So a recipe carries everything that used to be a decision in Python — the prompt, what
    it must not contain, how long it runs, how much of the artist it needs, and which
    reference class serves it best — and the planner becomes a function of it. Adding a
    format is adding a row.

    `builtin` marks the ones that ship. They can be copied and edited but not deleted, so
    a tenant always has somewhere to start.
    """

    id: str = Field(default_factory=lambda: new_id("rcp"))
    slug: str
    name: str
    intent: str = ""                       # one line: what this is for, in plain words
    modality: Modality = Modality.VIDEO
    builtin: bool = False

    # What gets sent.
    prompt_template: str = ""              # {artist} {title} {tempo} {section} {variant} {line} {aspect}
    negatives: list[str] = Field(default_factory=list)

    # The shape of the frame. Reaches the provider twice — as a param, and as the
    # `{aspect}` clause a template renders — from this one field, so the words the model
    # reads and the params it is sent cannot disagree. Templates used to type "vertical
    # 9:16" into the prose, which is what made this field decorative: a label asking for a
    # 16:9 cut got the metadata it asked for wrapped around a vertical picture.
    aspect_ratio: str = "9:16"

    # The spread. One asset per variant, cycled — this used to be one table of four moods
    # applied to every asset the app could make, and it belongs to the format that uses it
    # rather than to every format at once.
    variants: list[str] = Field(default_factory=list)

    # How long. `hook` follows the measured window, which is what a loop cut to a record
    # wants and is meaningless for a spoken line — a sentence takes as long as it takes,
    # and clipping it to a bar mid-word is worse than ignoring the grid.
    seconds_from: str = "hook"             # "hook" | "fixed" | "none"
    fixed_seconds: float | None = None

    # How much of the artist.
    face: FacePolicy = FacePolicy.NONE
    face_angle: FrameAngle | None = None   # which reference class this framing actually wants

    # Whether the master goes under this asset on delivery.
    #
    # True for every format whose picture sits under the record — a backdrop, a performance
    # clip — which is what `services.scoring` was written for and, for a while, what it
    # assumed about everything. `-map 0:v:0 -map 1:a:0` is exhaustive: the provider's own
    # track is not muted, it is absent. That is exactly right for a clip whose model-composed
    # soundtrack is a defect, and exactly wrong for one whose audio is the thing that was
    # bought. A direct-address clip is generated by instructing a model to *speak a line*;
    # scoring it replaced that line with the song, so the label paid for speech and was
    # handed a mute performance of it.
    #
    # Defaults to True because that is the older behaviour and the commoner case, so an
    # asset from before this field existed, or a tenant format that never considered the
    # question, keeps the record under it rather than silently losing it.
    score_with_master: bool = True

    approval: ApprovalState = ApprovalState.DRAFT

    @field_validator("slug")
    @classmethod
    def _slug_is_slug(cls, v: str) -> str:
        return slugify(v)

    def render(self, **fields: str) -> str:
        """Fill the template, dropping clauses whose fields are empty.

        A template is written for the richest case — a measured song, a named section, a
        line of dialogue — and most assets are not that. Rendering `. . ` where a tempo
        should have been is how a prompt acquires punctuation a model tries to interpret,
        so an empty field takes its whole clause with it.
        """
        out: list[str] = []
        for clause in self.prompt_template.split(". "):
            try:
                filled = clause.format(**{k: v or "" for k, v in fields.items()})
            except (KeyError, IndexError):
                # A placeholder this caller does not supply is not an error — it is a
                # clause that does not apply to this asset.
                continue
            if "{" in clause and not filled.replace(".", "").strip():
                continue
            if filled.strip(". "):
                out.append(filled.strip(". "))
        return ". ".join(out)


class Account(Base):
    """A person who may sign in to this tenant's console.

    The email is the identity — there is no password to store, reset, or leak. `id` is
    derived from the address rather than random (see `services.accounts.account_id`) so
    that "look up the account for this email" is a `get`, not a scan of the collection.

    `scopes` is populated but not yet enforced: no service calls `Principal.can()` today.
    It is written now because the alternative is backfilling authorisation onto accounts
    that already exist, and because an operator reading the YAML should be able to see
    what a person is allowed to do.
    """

    id: str
    email: str
    display_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class OtpChallenge(Base):
    """One outstanding sign-in code. Short-lived, hashed, attempt-limited.

    Stored rather than held in memory because request-and-verify are two requests that,
    on Lambda, land on two different instances — an in-memory map works perfectly on a
    laptop and fails only in production, which is the worst place to learn it.

    `code_hash` is an HMAC of the code under the session secret, never the code itself.
    The window is small and the code is six digits, so this is not a meaningful barrier
    to an attacker who already has the bucket; it is here so that a code sitting in
    object storage, in a log, or in a backup is not directly replayable.
    """

    id: str
    email: str
    code_hash: str
    expires_at: datetime
    attempts: int = 0

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utcnow()) > self.expires_at


class Artist(Base):
    """A roster member. First-class, not a string on a song (PRODUCT.md gap #1)."""

    id: str = Field(default_factory=lambda: new_id("art"))
    slug: str
    name: str
    bio: str | None = None
    links: dict[str, str] = Field(default_factory=dict)  # spotify, instagram, tiktok…
    consent: LikenessConsent = Field(default_factory=LikenessConsent)
    approval: ApprovalState = ApprovalState.DRAFT

    @field_validator("slug")
    @classmethod
    def _slug_is_slug(cls, v: str) -> str:
        return slugify(v)


class ReferenceFrame(BaseModel):
    """One stored frame plus the lighting setup it documents.

    The lighting label matters: MEMORY-SPEC's whole argument for why the second video
    is cheap is that the identity was built across *several* setups once.

    `sha256` is not decoration. When a frame is handed to a provider as a conditioning
    image, Genblaze derives both the step cache key and the manifest's canonical hash from
    the input asset's content hash — and falls back to its *URL* when there is none. On B2
    that URL is presigned and rotates on every render, so a frame without a hash means the
    cache never hits and the provenance record drifts every run. `pipeline.step()` warns
    about exactly this; the upload route now fills it in.
    """

    key: str  # object key in the bucket
    lighting: str = "neutral"
    # Which view this documents. Defaults to front because that is what every frame
    # uploaded before the classes existed actually is, and guessing anything else would
    # retroactively mislabel a stored reference set.
    angle: FrameAngle = FrameAngle.FRONT
    caption: str | None = None
    sha256: str | None = None
    # What the bytes are, so a provider handed this frame is told rather than left to
    # infer it from a presigned URL whose path carries a query string and no extension.
    content_type: str | None = None


class LockedStill(BaseModel):
    """A generated identity plate, kept so it is never generated twice.

    This is the index. The two-stage shot renders a still that settles the face in a
    scene, and then the clip is generated from it — so the still is the expensive,
    identity-resolving half, and it is *deterministic in its inputs*: the same faces, the
    same versions and the same scene prompt describe the same frame. Rendering it again is
    paying twice for an answer already stored.

    Keyed by a digest rather than by the prompt itself because the prompt is long, is
    edited freely, and would make an unwieldy dictionary key; the digest also covers the
    identity versions, so saving a new version of a face correctly misses the index rather
    than reusing a frame of how that person used to look.

    `approved` is the human gate, not a cache flag. A still somebody looked at and accepted
    is worth far more than one that merely exists — it is the frame every future clip in
    that scene will inherit — so the console can filter on it later without the model
    having to be changed again.
    """

    digest: str          # identity ids + versions + the still prompt
    key: str             # where the frame landed in the bucket
    sha256: str | None = None
    prompt: str = ""
    identity_ids: list[str] = Field(default_factory=list)
    approved: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Identity(Base):
    """The reusable "remap" — how this artist looks and reads on screen.

    This is the thing PRODUCT.md step 2 describes, and the reason the second video for
    an artist is cheap: built once, reused across every song and every kit.
    """

    id: str = Field(default_factory=lambda: new_id("idn"))
    artist_id: str
    # Which *person* this describes, when the artist is not one person.
    #
    # An `Artist` is a roster entry — a band is one entry with four faces in it, and until
    # now the model said an artist had one appearance and a stack of revisions to it. That
    # made a four-piece unrepresentable rather than merely awkward: whoever was described
    # last became the artist's face for every kit.
    #
    # So an identity belongs to a *line*, and versions count within their line. Empty is
    # the primary line — what every identity stored before this field existed is, and what
    # a solo artist never has to think about.
    name: str = ""
    version: int = 1
    structural_features: str | None = None  # face structure held invariant
    wardrobe: list[str] = Field(default_factory=list)
    reference_frames: list[ReferenceFrame] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)  # anti-drift
    approval: ApprovalState = ApprovalState.DRAFT

    # The standardised part of the description — three enums instead of three sentences.
    # See `compile()` for why that matters more than it looks.
    presentation: Presentation = Presentation.UNSPECIFIED
    build: BodyBuild = BodyBuild.UNSPECIFIED
    height: HeightBand = HeightBand.UNSPECIFIED

    # The index — stills already rendered for this face, by scene digest. Kept on the
    # identity rather than on the kit because its whole value is that it outlives the kit
    # that paid for it: the second kit in the same scene is free, which is MEMORY-SPEC's
    # "the second video is cheap" argument applied to the step that actually costs.
    #
    # Stored on the *line*, so a band member's stills are not offered for another member.
    locked_stills: list[LockedStill] = Field(default_factory=list)

    def still_for(self, digest: str) -> LockedStill | None:
        return next((s for s in self.locked_stills if s.digest == digest), None)

    # How many characters the identity may spend of a shot's prompt.
    #
    # This is the whole compression argument in one number. A prompt is not a document —
    # it is a fixed budget of attention, and every character the identity spends is a
    # character the *shot* does not get. The failure this prevents is specific and was
    # observed: an identity written as three paragraphs of prose pushes "vertical 9:16
    # loop, clean centre frame left empty" so far down the string that the model renders a
    # portrait instead of a backdrop, and the kit is wrong in a way that reads like the
    # shot description was ignored — because effectively it was.
    #
    # 220 is a working figure rather than a measured threshold, chosen so the identity
    # stays roughly a third of a typical composed prompt. It is a constant here so that
    # raising it is a deliberate edit in one place.
    PROMPT_BUDGET: ClassVar[int] = 220

    def compile(self, budget: int | None = None) -> str:
        """The identity, compressed to the text a provider actually sees.

        Ordered by salience, not by the order the form asks for things, because truncation
        happens at the end and the end is where the least important clause belongs:

        1. **Silhouette** — presentation, build, height. Three enums, a handful of
           characters, and the highest-signal thing in the record. It survives any
           truncation because it is first.
        2. **Face structure** — what the person typed. The one free-text field that earns
           its length, and the thing reference frames reinforce rather than replace.
        3. **Wardrobe** — last, because it is the most likely to be overridden by the shot
           and the least costly to lose.

        Clauses are dropped whole rather than cut mid-sentence. A prompt ending in "high
        cheekb" is worse than one that never mentioned cheekbones: it is a fragment the
        model will try to interpret.

        Kept on the model rather than in the Genblaze adapter so it is testable without a
        provider and identical across every adapter.
        """
        budget = self.PROMPT_BUDGET if budget is None else budget

        silhouette = " ".join(
            token
            for token in (
                self.presentation.value if self.presentation is not Presentation.UNSPECIFIED else "",
                self.build.value if self.build is not BodyBuild.UNSPECIFIED else "",
                f"{self.height.value} build" if self.height is not HeightBand.UNSPECIFIED else "",
            )
            if token
        )

        clauses = [
            silhouette,
            (self.structural_features or "").strip(),
            ("wearing " + ", ".join(self.wardrobe)) if self.wardrobe else "",
        ]

        kept: list[str] = []
        spent = 0
        for clause in clauses:
            clause = clause.strip(". ")
            if not clause:
                continue
            cost = len(clause) + (2 if kept else 0)  # the ". " join
            if spent + cost > budget:
                continue  # skipped whole, and the next clause still gets its chance
            kept.append(clause)
            spent += cost
        return ". ".join(kept)

    def prompt_fragment(self) -> str:
        """What the generator prepends. The compiled, budgeted form — see `compile`."""
        return self.compile()

    @property
    def dropped_clauses(self) -> int:
        """How many clauses the budget refused, for a screen that should say so.

        An identity that silently loses its wardrobe every render is an identity somebody
        will keep editing without understanding why nothing changes.
        """
        full = len([c for c in (
            self.presentation is not Presentation.UNSPECIFIED
            or self.build is not BodyBuild.UNSPECIFIED
            or self.height is not HeightBand.UNSPECIFIED,
            bool((self.structural_features or "").strip()),
            bool(self.wardrobe),
        ) if c])
        return max(0, full - len([c for c in self.compile().split(". ") if c]))

    def negative_fragment(self) -> str:
        return ", ".join(self.negatives)

    @property
    def display_name(self) -> str:
        """What to call this line on screen. The primary line has no name and needs one."""
        return self.name.strip() or "Primary"

    def plates(self, limit: int = 1, prefer: FrameAngle | None = None) -> list[ReferenceFrame]:
        """The frames worth *sending* — the conditioning images, not the whole upload set.

        A prompt describing a face is a lossy encoding of a face. An image of the face is
        not, which is why this exists: `prompt_fragment` was the only thing the generator
        ever saw, so the reference frames a label uploaded reached no provider at all.

        The selection is deliberately ordinary image bookkeeping rather than anything that
        needs a model or a new dependency, which is what makes it free to run:

        1. **Exact duplicates go**, by content hash. The same still uploaded twice is one
           reference, and a duplicate that survived would crowd out a genuinely different
           setup under the cap.
        2. **One per lighting setup**, in upload order. This is the whole reason
           `ReferenceFrame.lighting` exists — MEMORY-SPEC's argument for why the second
           video is cheap is that the identity was built across several setups once, and a
           plate set that is four exposures of one setup generalises to nothing.
        3. **Frames with no hash sort last.** They still work, but they cost a stable cache
           key and a stable manifest hash, so a hashed frame is preferred where there is a
           choice.

        `limit` defaults to one, and callers should think hard before raising it. Seedance
        routes a second image into `last_frame`, which does not mean "another reference" —
        it means "end the clip on this", and the model will interpolate towards it. Two
        plates on a video shot is a request for a morph, not a stronger likeness.
        """
        by_hash: dict[str, ReferenceFrame] = {}
        unhashed: list[ReferenceFrame] = []
        for frame in self.reference_frames:
            if frame.sha256:
                by_hash.setdefault(frame.sha256, frame)
            else:
                unhashed.append(frame)

        # Angle first, then lighting. A reference set covers a *head*, so the frame that
        # earns the one conditioning slot is the one whose view carries the most shape —
        # three-quarter, then front, then the profiles, then the back. Sorting by that
        # order rather than by upload order means a label can add frames in any sequence
        # and still get the best one sent.
        # What the *shot* wants comes first; the global order is only the tie-break.
        #
        # Ranking three-quarter above everything everywhere was wrong and wrong in the way
        # that matters: a selfie is a phone at arm's length, which is front-on, and it was
        # being handed a three-quarter because the ranking knew nothing about the framing
        # it was serving. A format that has an opinion states it; one that does not falls
        # back to the order below, which is still the best general answer.
        order = FrameAngle.by_reference_quality()
        ranked = sorted(
            [*by_hash.values(), *unhashed],
            key=lambda f: (f.angle is not prefer, order.index(f.angle)),
        )

        chosen: list[ReferenceFrame] = []
        seen: set[tuple[str, str]] = set()
        for frame in ranked:
            setup = (frame.angle.value, (frame.lighting or "neutral").strip().lower())
            if setup in seen:
                continue
            seen.add(setup)
            chosen.append(frame)
            if len(chosen) >= limit:
                break
        return chosen

    @property
    def coverage(self) -> dict[FrameAngle, list[ReferenceFrame]]:
        """The reference set as classes rather than as a pile, in preference order.

        This is what makes "collect many stills" a finishable task instead of an open one:
        an angle with no frames is a visible gap, and an angle with nine is visibly done.
        Every class appears, including the empty ones — a coverage view that hides what is
        missing is a progress bar that only counts forwards.
        """
        grouped: dict[FrameAngle, list[ReferenceFrame]] = {
            angle: [] for angle in FrameAngle.by_reference_quality()
        }
        for frame in self.reference_frames:
            grouped[frame.angle].append(frame)
        return grouped

    @property
    def covered_angles(self) -> int:
        return len([frames for frames in self.coverage.values() if frames])

    @property
    def has_plates(self) -> bool:
        return bool(self.plates(limit=1))


class HookWindow(BaseModel):
    """Pillar 13's one free lever, made a first-class input rather than an afterthought.

    Retained now that `Song.sections` exists, because it is the *primary* hook — the one
    window a kit cuts to when the brief names no section — and because every song already
    stored in a bucket has one. `SongService.set_primary_section` keeps it identical to
    the primary section's window, so the two can never disagree about what a kit will do.
    """

    start_ms: int = 0
    end_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


class SongSection(BaseModel):
    """One named stretch of the song. A song has many; several may be hooks.

    A single `hook` window said a song has exactly one loopable moment, which is false of
    every song with a chorus that comes back. The sections list is the fix, and it carries
    the same discipline the BPM does: a section whose numbers came from the analyser
    records `Provenance.MEASURED` and the method that produced it, and one a person typed
    records `MANUAL`. A window with no stated origin is how a guess gets promoted to a
    measurement between one screen and the next.

    `energy_low_band` and `energy_rms_db` are only ever set by the analyser — they are the
    per-bar kick energy and loudness of this stretch, and they are what
    `services.recommendations` ranks on. A section a person drew by hand has neither, and
    therefore gets ranked on what *is* known about it rather than on a fabricated score.

    The texture fields below carry the same rule one step further. `segment_type` says
    which *other* sections are the same material, `evidence` says why this one is called
    what it is called, and `band_mix`/`tonal_mid`/`brightness_hz`/`onset_density` say what
    it is made of — which is the difference between a section a person can choose between
    and a float they have to take on trust. All of them are `None` or empty on a
    hand-drawn section for the same reason the energies are: the analyser is the only
    thing that can know them, and inventing them would make a window somebody dragged look
    like one somebody measured.
    """

    id: str = Field(default_factory=lambda: new_id("sec"))
    role: SectionRole = SectionRole.HOOK
    label: str = ""
    start_ms: int = 0
    end_ms: int = 0
    source: Provenance = Provenance.MANUAL
    method: str | None = None
    notes: str | None = None

    # Measured features. None on a hand-drawn section, and nothing invents them.
    energy_low_band: float | None = None
    energy_rms_db: float | None = None
    beats: int | None = None

    # Where it sits on the bar grid — the unit a loop is allowed to be cut in.
    bar_start: int | None = None
    bar_end: int | None = None

    # What it is made of. `band_mix` is sub / low-mid / presence / air as proportions of
    # this section's energy; `tonal_mid` is the tonal share of the band a lead vocal sits
    # in, and is a proxy rather than a vocal detector — see `ports.audio.BarFeature`.
    band_mix: list[float] = Field(default_factory=list)
    brightness_hz: float | None = None
    onset_density: float | None = None
    tonal_mid: float | None = None

    # How it relates to the rest of the song, and why it is named what it is named.
    segment_type: str | None = None
    repeat_of: str | None = None
    evidence: list[str] = Field(default_factory=list)
    entry: str | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def window(self) -> HookWindow:
        return HookWindow(start_ms=self.start_ms, end_ms=self.end_ms)

    @property
    def is_hook_material(self) -> bool:
        return self.role.is_hook_material

    @property
    def display_name(self) -> str:
        return self.label.strip() or self.role.value


class SongBar(BaseModel):
    """One bar of the measured song. The curve the console draws under the sections.

    Stored per bar rather than summarised because a section boundary is an assertion about
    a shape, and a screen that shows the boundary without the shape asks to be believed
    instead of checked. A four-minute track is about 120 of these, which is a rounding
    error next to the master they were measured from.
    """

    index: int
    start_ms: int
    sub: float
    low_mid: float
    presence: float
    air: float
    centroid_hz: float
    onsets: float
    rms_db: float
    tonal_mid: float

    @property
    def total(self) -> float:
        """Stacked height — the bar's energy on the shared scale the bands are on."""
        return self.sub + self.low_mid + self.presence + self.air


class SongAnalysis(BaseModel):
    """What an analyser found in the master, and how.

    Stored on the song rather than in a side collection because it is a property *of* the
    song and is read on every screen that shows one — and because a measurement whose
    method has to be joined in from somewhere else is a measurement the UI will eventually
    render without it.

    `status` is here for the same reason `KitStatus` is on a kit: analysis is a queued job
    (§2b rule 3), so there is a window in which the song is being measured and the console
    must be able to say so instead of showing an empty panel.
    """

    status: AnalysisStatus = AnalysisStatus.QUEUED
    analyzer: str | None = None       # which adapter ran
    method: str | None = None         # the one-line description of how, verbatim from it
    source_key: str | None = None     # which master was analysed
    source_sha256: str | None = None  # ...and exactly which bytes
    requested_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    error: str | None = None

    # Findings. Every one of these is optional because an analyser is allowed to fail to
    # find something and say so — a `None` here means "not measured", never "zero".
    duration_ms: int | None = None
    bpm: float | None = None
    beat_ms: float | None = None
    downbeat_ms: int | None = None
    drop_ms: int | None = None
    riser_beats: int | None = None    # RHYTHM-STUDY §3 — the track's own gain ramp
    plateau_beats: int | None = None  # §4 — how long the drop holds full energy
    bar_phase_confidence: float | None = None  # §2's residue test, as a fraction
    warnings: list[str] = Field(default_factory=list)

    # The per-bar curve the sections were cut from, and the arrangement written out.
    # `sheet` is assembled by the adapter from these numbers and nothing else — it is what
    # gets handed to anything that reasons in language rather than in floats, and its
    # whole value is that no model wrote a word of it. See `ports.audio`.
    bars: list[SongBar] = Field(default_factory=list)
    sheet: str | None = None

    @property
    def bar_ms(self) -> float | None:
        return self.beat_ms * 4 if self.beat_ms else None

    @property
    def peak_bar_energy(self) -> float:
        """The tallest stacked bar, for a chart that needs a ceiling. Never zero."""
        return max((bar.total for bar in self.bars), default=1.0) or 1.0

    @property
    def ran(self) -> bool:
        return self.status is AnalysisStatus.DONE


class LyricLine(BaseModel):
    """One line of the song's words, and where it is heard.

    Same provenance discipline as `SongSection`, applied to language instead of energy:
    `MEASURED` means a transcriber heard it and `method` says which one, `MANUAL` means a
    person typed or corrected it. The difference matters more here than anywhere else in
    the model — these words get attributed to the artist, and a screen that renders a
    model's guess and a person's correction identically is a screen that launders one into
    the other.

    `confidence` is the transcriber's, 0–1, and only ever survives on a `MEASURED` line.
    A line somebody edited has no confidence, because the number described audio the
    provider heard and not the sentence a person decided it was.
    """

    id: str = Field(default_factory=lambda: new_id("lyr"))
    text: str = ""
    start_ms: int = 0
    end_ms: int = 0
    source: Provenance = Provenance.MANUAL
    method: str | None = None
    confidence: float | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def is_uncertain(self) -> bool:
        """Worth checking by ear. The console flags these rather than hiding them.

        0.6 is a threshold, not a measurement, and it is here rather than in a template so
        that "which lines did we flag" is one answer the whole app agrees on. Sung vocal
        over a loud mix sits lower than speech does, so this is deliberately generous —
        the cost of flagging a correct line is a glance, and the cost of not flagging a
        wrong one is a wrong lyric on a released clip.
        """
        return self.source is Provenance.MEASURED and self.confidence is not None and self.confidence < 0.6


class SongLyrics(BaseModel):
    """What a transcriber heard in the master, and how — plus every correction since.

    Stored on the song for the same reason `SongAnalysis` is: it is a property of the song,
    read on every screen that shows one, and a claim whose method has to be joined in from
    elsewhere is a claim the UI will eventually render without it.

    `status` reuses `AnalysisStatus` rather than declaring a parallel enum with identical
    members. Transcription is a queued job with exactly the same four states, and two
    enums that must stay in lockstep are one enum with a maintenance burden.
    """

    status: AnalysisStatus = AnalysisStatus.QUEUED
    transcriber: str | None = None      # which adapter ran
    method: str | None = None           # its one-line description of how, verbatim
    source_key: str | None = None       # which master was heard
    source_sha256: str | None = None    # ...and exactly which bytes
    requested_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    error: str | None = None

    language: str | None = None
    language_confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    lines: list[LyricLine] = Field(default_factory=list)

    def line(self, line_id: str) -> LyricLine | None:
        return next((line for line in self.lines if line.id == line_id), None)

    @property
    def ordered_lines(self) -> list[LyricLine]:
        """Song order — which is not quite the same as sorting by start time.

        `SongSection.ordered_sections` can sort on the window alone because every section
        has one. A lyric line is allowed not to: a line typed off a sheet has no timing,
        and sorting those by `start_ms` puts every one of them at 0 — so a line somebody
        appended to the last chorus jumps to the top of the song, above the intro.

        So an untimed line inherits the position of the last timed line before it in list
        order, and the list index breaks the tie. A line added after the drop stays after
        the drop, a lyric with no timings at all keeps the order it was typed in, and a
        timed lyric sorts by its windows exactly as before.
        """
        carried = 0
        keyed: list[tuple[int, int, LyricLine]] = []
        for index, line in enumerate(self.lines):
            if line.start_ms or line.end_ms:
                carried = line.start_ms
            keyed.append((carried, index, line))
        return [line for _, _, line in sorted(keyed, key=lambda item: item[:2])]

    @property
    def text(self) -> str:
        """The lyric as one block — what a brief or a prompt is handed."""
        return "\n".join(line.text for line in self.ordered_lines if line.text.strip())

    @property
    def ran(self) -> bool:
        return self.status is AnalysisStatus.DONE

    @property
    def edited_lines(self) -> list[LyricLine]:
        """Lines a person owns. Re-transcribing discards these, so it asks first."""
        return [line for line in self.lines if line.source is Provenance.MANUAL]

    @property
    def uncertain_lines(self) -> list[LyricLine]:
        return [line for line in self.ordered_lines if line.is_uncertain]

    def lines_in(self, start_ms: int, end_ms: int) -> list[LyricLine]:
        """The words inside a window — what a kit cutting to that section would be over.

        Overlap rather than containment: a line that starts before the section and runs
        into it is audible in the cut, and a hook whose first word is clipped is a fact
        about the window worth seeing on the screen where the window is chosen.
        """
        return [
            line
            for line in self.ordered_lines
            if line.end_ms > start_ms and line.start_ms < end_ms
        ]


class Song(Base):
    """One measured master. Measured once; the measurement is reused forever.

    FORMAT-SPEC requires the provenance of a BPM, not just the number — `bpm_method`
    carries the justification, and the UI refuses to hide it. `sections` extends that rule
    from the tempo to the arrangement: a song has as many hooks as it has, each with its
    own window and its own record of where that window came from.
    """

    id: str = Field(default_factory=lambda: new_id("sng"))
    artist_id: str
    slug: str
    title: str
    bpm: float | None = None
    bpm_method: str | None = None
    drop_ms: int | None = None
    hook: HookWindow = Field(default_factory=HookWindow)
    sections: list[SongSection] = Field(default_factory=list)
    primary_section_id: str | None = None
    analysis: SongAnalysis | None = None
    lyrics: SongLyrics | None = None
    master_key: str | None = None  # owned master in the bucket, private
    master_content_type: str | None = None
    isrc: str | None = None
    spotify_url: str | None = None
    approval: ApprovalState = ApprovalState.DRAFT

    @field_validator("slug")
    @classmethod
    def _slug_is_slug(cls, v: str) -> str:
        return slugify(v)

    # -- reads over the sections list -------------------------------------------
    def section(self, section_id: str) -> SongSection | None:
        return next((s for s in self.sections if s.id == section_id), None)

    @property
    def ordered_sections(self) -> list[SongSection]:
        return sorted(self.sections, key=lambda s: (s.start_ms, s.end_ms))

    @property
    def hook_sections(self) -> list[SongSection]:
        """The loopable ones, in song order. What the generate screen offers."""
        return [s for s in self.ordered_sections if s.is_hook_material and s.duration_ms > 0]

    @property
    def primary_section(self) -> SongSection | None:
        return self.section(self.primary_section_id) if self.primary_section_id else None

    @property
    def has_hook(self) -> bool:
        """Either shape counts: a primary window, or at least one loopable section."""
        return self.hook.duration_ms > 0 or bool(self.hook_sections)

    # -- reads over the lyric ----------------------------------------------------
    @property
    def has_lyrics(self) -> bool:
        return bool(self.lyrics and self.lyrics.lines)

    def lyrics_for(self, section: SongSection) -> list[LyricLine]:
        """The words a kit cut to this section would be over. Empty if none are known."""
        if not self.lyrics:
            return []
        return self.lyrics.lines_in(section.start_ms, section.end_ms)


class Asset(BaseModel):
    """One Genblaze step output."""

    id: str = Field(default_factory=lambda: new_id("ast"))
    modality: Modality
    provider: str
    model: str
    prompt: str | None = None
    key: str | None = None  # where the bytes landed
    url: str | None = None
    sha256: str | None = None
    # `None` means the price is genuinely unknown, and it is a different fact from `0`.
    # Genblaze registers no prices of its own ("the SDK ships zero hardcoded prices as of
    # 0.3.0"), so `step.cost_usd` is None for any model `adapters/pricing` has not
    # registered. Coercing that to 0 is what let a multi-dollar validation run report a
    # $0.00 ledger on 2026-07-31.
    cost_cents: int | None = 0
    cost_note: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    # --- provenance: what this asset was made from ---------------------------------
    #
    # The model and the prompt were already here, which answers "how was this made" for a
    # text-to-video call and answers nothing at all once assets are conditioned on other
    # assets. A clip generated from a still that was generated from a photograph of a real
    # person has three ancestors, and "which base did this come out of" should be readable
    # off the asset rather than reconstructed from a kit brief that may since have been
    # edited or deleted.
    #
    # All four are recorded at collection time and travel into the manifest, so they
    # survive the records they point at.
    recipe_slug: str | None = None            # which format this is an instance of
    identity_refs: list[dict[str, Any]] = Field(default_factory=list)  # id + name + version
    reference_keys: list[str] = Field(default_factory=list)   # the frames it was conditioned on
    derived_from: list[str] = Field(default_factory=list)     # keys of assets it was generated from

    # --- the record under the picture ----------------------------------------------
    #
    # For a format whose picture sits under the master, a clip carrying a soundtrack the
    # model composed for itself is a defect — that is the 2026-07-31 finding, and the
    # negatives in `recipes.SILENT_NEGATIVES` were only ever a conditioning signal against
    # it. `services.scoring` writes a second object per such video: the same frames, with
    # the master's own audio mapped in place of whatever the provider returned. A format
    # whose audio is the asset — a spoken line — sets `Recipe.score_with_master` false and
    # keeps what the provider sent.
    #
    # `key` still points at the provider's bytes and is never overwritten. That is what
    # keeps the content hash in the manifest meaning what it says; the scored copy is a
    # derived object with its own key, and `playable_key` is the one a person should be
    # shown or handed.
    scored_key: str | None = None
    audio_source_key: str | None = None   # the master those seconds came from
    audio_start_ms: int | None = None     # where in the record this clip starts
    audio_end_ms: int | None = None       # the end of the hook window it was cut to
    # Always set on a video asset, whichever way it went. "The master was laid under it"
    # and "the master could not be laid under it" look identical on a screen that only
    # renders the successful case, and the second one is a clip scored by a model.
    audio_note: str | None = None

    @property
    def playable_key(self) -> str | None:
        """What to show, download, and hand to a fan — the scored cut when there is one."""
        return self.scored_key or self.key


class KitAttempt(BaseModel):
    """One run of a kit that was thrown away, kept because it was paid for.

    A retry re-plans the brief from scratch and overwrites `assets`, `run_id` and the
    ledger with the new attempt's. Doing that silently would make a kit that burned
    $3.40 on two failed video steps and then succeeded read as though it had only ever
    cost the successful run — which is the same class of defect as pricing a modality
    instead of a model: a total that is precise and wrong.

    So the attempt is recorded here first, and `Kit.spent_cents` adds it back. What
    cannot be kept is the bytes: the previous attempt's objects are deleted from the
    bucket at retry time, because `run()` would otherwise orphan them and the label
    would pay storage for objects no screen can reach.
    """

    number: int                      # 1 for the original run
    status: KitStatus                # what it ended as — always `failed` today
    error: str | None = None
    cost_cents: int = 0
    asset_count: int = 0
    run_id: str | None = None        # the Genblaze run, for reading provider-side logs
    at: datetime = Field(default_factory=utcnow)


class Kit(Base):
    """A pack of assets for one release, in one format = one Genblaze Run.

    "In one format" is the part worth stating: a kit is a `Recipe` applied to a song, not a
    fixed kind of output. Backdrop loops a fan can put themselves in front of are one
    format; a press still, a cinematic performance cut and an announcement to camera are
    others, and nothing here privileges the first.

    `run_id` is the Genblaze run; `parent_run_id` is reserved for the deferred fan
    composite path, where lineage from a finished fan clip back to the exact AI assets
    is the thing worth showing a judge (BUILD-SPEC §7b).
    """

    id: str = Field(default_factory=lambda: new_id("kit"))
    artist_id: str
    song_id: str
    name: str
    status: KitStatus = KitStatus.QUEUED
    approval: ApprovalState = ApprovalState.DRAFT
    run_id: str | None = None
    parent_run_id: str | None = None
    manifest_key: str | None = None
    manifest_verified: bool | None = None
    assets: list[Asset] = Field(default_factory=list)
    total_cost_cents: int = 0
    error: str | None = None
    brief: dict[str, Any] = Field(default_factory=dict)
    # Every run of this kit that was discarded, oldest first. Empty on a kit that has
    # only ever run once, which is nearly all of them.
    attempts: list[KitAttempt] = Field(default_factory=list)

    def recost(self) -> None:
        self.total_cost_cents = sum(a.cost_cents or 0 for a in self.assets)

    @property
    def attempt(self) -> int:
        """Which run this is. 1 until somebody retries."""
        return len(self.attempts) + 1

    @property
    def abandoned_cost_cents(self) -> int:
        """What the discarded attempts cost. Money spent that delivered nothing."""
        return sum(a.cost_cents for a in self.attempts)

    @property
    def spent_cents(self) -> int:
        """Everything this kit has cost the label, not just what survived.

        `total_cost_cents` is the ledger for the assets that exist right now, which is
        the right number under an asset grid and the wrong one on an invoice.
        """
        return self.total_cost_cents + self.abandoned_cost_cents

    @property
    def cost_is_complete(self) -> bool:
        """False when any asset's price is unknown, so a total can be shown as a floor
        rather than as the invoice. A kit that says `$0.42` when one of its three steps
        was never priced is claiming precision it does not have."""
        return all(a.cost_cents is not None for a in self.assets)
