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
    """Pillar 13's one free lever, made a first-class input rather than an afterthought."""

    start_ms: int = 0
    end_ms: int = 0

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


class Song(Base):
    """One measured master. Measured once; the measurement is reused forever.

    FORMAT-SPEC requires the provenance of a BPM, not just the number — `bpm_method`
    carries the justification, and the UI refuses to hide it.
    """

    id: str = Field(default_factory=lambda: new_id("sng"))
    artist_id: str
    slug: str
    title: str
    bpm: float | None = None
    bpm_method: str | None = None
    drop_ms: int | None = None
    hook: HookWindow = Field(default_factory=HookWindow)
    master_key: str | None = None  # owned master in the bucket, private
    isrc: str | None = None
    spotify_url: str | None = None
    approval: ApprovalState = ApprovalState.DRAFT

    @field_validator("slug")
    @classmethod
    def _slug_is_slug(cls, v: str) -> str:
        return slugify(v)


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
