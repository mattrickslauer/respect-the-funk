"""Request bodies for the JSON API.

Responses are the domain models themselves — they are already pydantic, already
tenant-stamped, and inventing a parallel set of DTOs would be two things to keep in
sync for no gain at this size.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from remixkit.domain.models import ApprovalState, SectionRole


class RequestCodeIn(BaseModel):
    email: str


class VerifyCodeIn(BaseModel):
    email: str
    code: str


class ArtistIn(BaseModel):
    name: str
    bio: str | None = None
    links: dict[str, str] = Field(default_factory=dict)


class ArtistPatch(BaseModel):
    name: str | None = None
    bio: str | None = None
    links: dict[str, str] | None = None


class ConsentIn(BaseModel):
    granted: bool
    signed_by: str | None = None
    notes: str | None = None


class ApprovalIn(BaseModel):
    state: ApprovalState


class IdentityIn(BaseModel):
    structural_features: str | None = None
    wardrobe: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)


class SongIn(BaseModel):
    title: str
    bpm: float | None = None
    bpm_method: str | None = None
    isrc: str | None = None
    spotify_url: str | None = None


class MeasurementIn(BaseModel):
    bpm: float | None = None
    bpm_method: str | None = None
    drop_ms: int | None = None


class HookIn(BaseModel):
    start_ms: int
    end_ms: int


class SectionIn(BaseModel):
    """One hook (or verse, or breakdown) marked by hand.

    No `source` field: anything arriving through this route is a person saying so, and
    letting a caller declare its own window `measured` would make the provenance flag
    meaningless. Only `services.songs.apply_analysis` writes that.
    """

    start_ms: int
    end_ms: int
    role: SectionRole = SectionRole.HOOK
    label: str = ""
    notes: str | None = None
    make_primary: bool = False


class SectionPatch(BaseModel):
    start_ms: int | None = None
    end_ms: int | None = None
    role: SectionRole | None = None
    label: str | None = None
    notes: str | None = None


class UploadUrlIn(BaseModel):
    content_type: str = "audio/wav"


class MasterIn(BaseModel):
    """The key the browser just PUT to. Checked against the song before it is recorded."""

    key: str
    content_type: str | None = None


class KitIn(BaseModel):
    song_id: str
    name: str | None = None
    video_count: int = 3
    hook_lines: list[str] = Field(default_factory=list)
    tts_text: str | None = None
    budget_cents: int | None = None
    # Which hooks this kit is cut to. Empty means the song's primary hook window, which is
    # what every kit meant before a song could have several.
    section_ids: list[str] = Field(default_factory=list)
