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
    # What this shot is *for*, in one phrase — the mood and the hook it was cut to. Not
    # sent to any provider: it exists so the cost screen can show which hook each video in
    # the plan belongs to, which is the only way to read a multi-hook brief.
    label: str = ""
    # Per-shot anti-drift, merged with the identity's own negatives at resolution time.
    # Separate from `Identity.negatives` because they answer different questions: the
    # identity's say "this is not what the artist looks like", a shot's say "this is not
    # what this *kind of output* should contain" — a backdrop loop that must not score
    # itself, a lyric card that must not hallucinate a second caption.
    negatives: list[str] = field(default_factory=list)
    # Whether this shot should be conditioned on the artist's face.
    #
    # Opt-in per shot rather than "always, if an identity exists", because a lyric card is
    # a typographic plate and putting a face reference on it produces a portrait with words
    # over it. The loops want the face held invariant; the cards want the text legible.
    use_identity_plate: bool = False
    # Render an identity-locked still first, then generate the clip *from that still*.
    #
    # This is the two-stage character shot, and it is the strongest likeness this stack
    # can produce without training a model. A video model conditioned only on a text
    # description drifts across the clip; one conditioned on a first frame is anchored to
    # it. So the face is settled in a still — which costs a few cents, can be regenerated
    # until it is right, and can be looked at by a person — and the clip inherits it
    # rather than re-deriving it.
    identity_lock: bool = False


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


@dataclass
class PlannedShot:
    """One shot resolved to exactly what would go on the wire — without sending it.

    This is the preview unit. A `ShotSpec` is what the brief *asked for*; a `PlannedShot`
    is what the provider would actually receive, after model resolution, prompt
    composition, duration snapping, and parameter assembly. The distinction is the whole
    point: on 2026-07-31 a kit was bought against a table showing `shot.prompt`, which is
    the mood fragment — not the composed string a model saw. The composed string named no
    song, so the model rendered a stock night drive and scored it with music of its own.

    Nothing here performs I/O or spends anything. `estimate_cents` is priced by the same
    `adapters.pricing` book the ledger uses, and `skipped_reason` says why a shot in the
    brief would produce nothing — which is a fact worth showing before the button rather
    than discovering in a failed run.
    """

    index: int
    modality: Modality
    label: str
    provider: str | None          # the adapter class that would run it
    vendor: str                   # gmicloud | openai | google | elevenlabs | mock | none
    model: str | None
    prompt: str                   # composed — identity fragment + shot, as sent
    negative_prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    requested_seconds: float | None = None
    rendered_seconds: int | None = None
    reference_keys: list[str] = field(default_factory=list)
    # Why the face is or is not going with this shot, in one phrase. Present even when
    # everything worked, because "the plate was attached" and "the plate was silently
    # dropped" look identical on a screen that only shows the successful case — and the
    # second one is how an identity a label spent an afternoon building reaches nothing.
    plate_note: str | None = None
    # The identity-locked still this shot is generated *from*, if it has one.
    #
    # Modelled as a nested step rather than as a second entry in the plan so that
    # `PlannedShot.index` stays the index of the `ShotSpec` it came from — which is what
    # per-shot prompt overrides are keyed by. A two-stage shot is one thing a person
    # bought, rendered in two calls, and the plan reads that way.
    prelude: PlannedShot | None = None
    # True on the still itself. `_resolve` yields preludes flat as well as nested — flat
    # because the pipeline builder submits them as their own step, nested because the plan
    # displays and prices them as part of the shot they belong to. Without this marker the
    # plan counts and charges each two-stage shot's still twice.
    is_prelude: bool = False
    estimate_cents: int = 0
    skipped_reason: str | None = None

    @property
    def runs(self) -> bool:
        return self.skipped_reason is None

    @property
    def duration_was_snapped(self) -> bool:
        """The brief asked for a length the model does not render.

        Worth surfacing on its own rather than leaving the reader to compare two numbers:
        a loop shorter than its hook is the one thing a fan copying the template notices.
        """
        if self.requested_seconds is None or self.rendered_seconds is None:
            return False
        return abs(self.requested_seconds - self.rendered_seconds) >= 0.05


@dataclass
class GenerationPlan:
    """What a run would do, priced, with nothing submitted.

    `estimate_cents` is the sum over shots that would actually run. A skipped shot costs
    nothing and must not be quoted, or the estimate describes a run that cannot happen.
    """

    shots: list[PlannedShot] = field(default_factory=list)
    blocker: str | None = None  # why the whole run would refuse, if it would

    @property
    def runnable(self) -> list[PlannedShot]:
        return [s for s in self.shots if s.runs]

    @property
    def skipped(self) -> list[PlannedShot]:
        return [s for s in self.shots if not s.runs]

    @property
    def estimate_cents(self) -> int:
        """Both stages of every runnable shot.

        A two-stage shot is billed twice — the still and the clip — and quoting only the
        clip would understate a character kit by the price of an image per loop.
        """
        return sum(
            s.estimate_cents + (s.prelude.estimate_cents if s.prelude else 0)
            for s in self.runnable
        )

    @property
    def steps(self) -> int:
        """Provider calls, not shots. What the run will actually submit."""
        return sum(2 if s.prelude else 1 for s in self.runnable)


class Generator(Protocol):
    name: str

    def plan(self, request: GenerationRequest) -> GenerationPlan:
        """Resolve the request to per-shot wire payloads. Spends nothing, sends nothing.

        Must resolve models, prompts, durations, and params by the *same* code path
        `generate` uses. A preview built from a second implementation is a preview of a
        different run, which is worse than no preview at all — it is a claim about the
        spend that nothing enforces.
        """

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Fan out, write assets + manifest to storage, return what landed.

        Must be idempotent on `request.kit_id` (BUILD-SPEC §2b rule 4) — SQS and Batch
        both redeliver, and a double-delivered kit must cost nothing the second time.
        """
