"""The generation port.

Deliberately expressed in *product* terms — an artist identity, a song's hook window,
a set of shots — not in Genblaze terms. The Genblaze `Pipeline`, its providers, and
its sink all live behind this line in `adapters/generator_genblaze.py`. That is what
lets the same service code run against `MockGenerator` with zero credentials, which is
how this ships before provider keys exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from remixkit.domain.models import Asset, Identity, Modality, Song


@dataclass
class ShotSpec:
    """One requested output. A kit is a handful of these."""

    modality: Modality
    prompt: str
    seconds: float | None = None
    aspect_ratio: str = "9:16"
    model: str | None = None  # None → the adapter's configured default for this modality


@dataclass
class GenerationRequest:
    kit_id: str
    tenant_id: str
    artist_name: str
    identity: Identity | None
    song: Song
    shots: list[ShotSpec] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    run_id: str | None
    assets: list[Asset]
    manifest_key: str | None = None
    manifest_verified: bool | None = None
    error: str | None = None


class Generator(Protocol):
    name: str

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Fan out, write assets + manifest to storage, return what landed.

        Must be idempotent on `request.kit_id` (BUILD-SPEC §2b rule 4) — SQS and Batch
        both redeliver, and a double-delivered kit must cost nothing the second time.
        """
