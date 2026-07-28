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
from typing import Any

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
    """

    key: str  # object key in the bucket
    lighting: str = "neutral"
    caption: str | None = None
    sha256: str | None = None


class Identity(Base):
    """The reusable "remap" — how this artist looks and reads on screen.

    This is the thing PRODUCT.md step 2 describes, and the reason the second video for
    an artist is cheap: built once, reused across every song and every kit.
    """

    id: str = Field(default_factory=lambda: new_id("idn"))
    artist_id: str
    version: int = 1
    structural_features: str | None = None  # face structure held invariant
    wardrobe: list[str] = Field(default_factory=list)
    reference_frames: list[ReferenceFrame] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)  # anti-drift
    approval: ApprovalState = ApprovalState.DRAFT

    def prompt_fragment(self) -> str:
        """The identity, rendered into the text a provider actually sees.

        Kept here rather than in the Genblaze adapter so it is testable without a
        provider and identical across every adapter.
        """
        bits: list[str] = []
        if self.structural_features:
            bits.append(self.structural_features)
        if self.wardrobe:
            bits.append("wearing " + ", ".join(self.wardrobe))
        return ". ".join(bits)

    def negative_fragment(self) -> str:
        return ", ".join(self.negatives)


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

    @property
    def bar_ms(self) -> float | None:
        return self.beat_ms * 4 if self.beat_ms else None

    @property
    def ran(self) -> bool:
        return self.status is AnalysisStatus.DONE


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
    cost_cents: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


class Kit(Base):
    """A pack of templatable assets for one release = one Genblaze Run.

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

    def recost(self) -> None:
        self.total_cost_cents = sum(a.cost_cents for a in self.assets)
