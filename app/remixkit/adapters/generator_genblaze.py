"""The core: a kit is one Genblaze `Run` whose steps fan out into templatable assets.

This is BUILD-SPEC §4 against the API as it actually ships in genblaze-core 0.3.7 —
`Pipeline(name, tenant_id=…)`, `.step(provider, model=, prompt=, modality=)`,
`.run(sink=…, timeout=…)`, returning a `PipelineResult` carrying `.run` and `.manifest`.

Three things here are load-bearing beyond "call the API":

**The identity is prepended to every prompt.** That is what makes the artist look like
themselves across a kit, and it is why the second kit for an artist is cheap — the
expensive part (deciding how they read on screen) was done once and stored. The
anti-drift negatives ride along as `negative_prompt`.

**The sink is `HIERARCHICAL`.** `runs/{tenant}/{date}/{run_id}/…` — the tenant is a
path component, so B2's layout, the documents, and the manifests share one tenant axis
(§2b rule 6).

**The manifest is verified before a kit is allowed to be `ready`.** `manifest.verify()`
is the gate, not a decoration: an unverified manifest means the provenance claim is
unsupported, and the whole disclosure argument rests on that claim being true.
"""

from __future__ import annotations

import logging
from typing import Any

from remixkit.adapters import pricing
from remixkit.adapters import providers as provider_table
from remixkit.domain.models import Asset, Modality
from remixkit.ports.generator import (
    GenerationPlan,
    GenerationRequest,
    GenerationResult,
    PlannedShot,
)

log = logging.getLogger(__name__)

_MODALITY_MAP = {
    Modality.VIDEO: "VIDEO",
    Modality.IMAGE: "IMAGE",
    Modality.AUDIO: "AUDIO",
}


class GenblazeGenerator:
    """Runs the fan-out. `backend='mock'` swaps the provider table and nothing else."""

    def __init__(
        self,
        storage: Any,
        *,
        backend: str = "mock",
        prefix: str = "remixkit",
        models: dict[Modality, str] | None = None,
        timeout_s: float = 900.0,
        max_concurrency: int = 4,
        max_run_cents: int = 0,
        reference_slot: str = "",
        reference_max: int = 4,
    ) -> None:
        self.name = f"genblaze:{backend}"
        self._storage = storage
        self._backend = backend
        self._prefix = prefix
        self._models = models or {}
        self._timeout = timeout_s
        self._max_concurrency = max_concurrency
        self._max_run_cents = max_run_cents
        self._reference_slot = (reference_slot or "").strip()
        self._reference_max = max(1, reference_max)

    # -- sink -------------------------------------------------------------------
    def _build_sink(self):
        from genblaze_core.storage import KeyStrategy, ObjectStorageSink

        backend = self._storage.as_genblaze_backend()
        return ObjectStorageSink(
            backend,
            prefix=self._prefix,
            key_strategy=KeyStrategy.HIERARCHICAL,
        )

    # -- planning ---------------------------------------------------------------
    def model_plan(self, shots: Any) -> list[tuple[Modality, str | None, float | None]]:
        """Which model each shot would actually run on, without running anything.

        The budget gate needs this before a kit is queued, and it has to resolve models
        the *same* way `generate` does or the quote is for a different run than the one
        that executes. Resolution depends on which providers are keyed right now, so this
        cannot be a static table.
        """
        table = provider_table.resolve(self._backend)
        plan: list[tuple[Modality, str | None, float | None]] = []
        for shot in shots:
            _provider, model = self._resolve_model(table, shot)
            # Snapped, so the quote prices the clip that will actually be
            # rendered rather than the one the brief asked for.
            plan.append((shot.modality, model, provider_table.snap_duration(model, shot.seconds)))
        return plan

    def _resolve_model(self, table: dict, shot: Any) -> tuple[Any, str | None]:
        """Which provider answers for this shot, and which model it is asked for.

        Explicit shot > operator override > the model this provider actually serves. The
        last step is not a nicety: provider selection happens at runtime, so a single
        configured id would be sent to whichever provider answered — a GMI slug to Sora,
        which 404s.

        One function because three callers need the same answer: the quote (`model_plan`),
        the preview (`_resolve`), and the run (`generate`). They disagreed once and it cost
        real money.
        """
        provider = table.get(shot.modality)
        model = (
            shot.model
            or self._models.get(shot.modality)
            or (provider_table.default_model(provider, shot.modality) if provider else None)
        )
        return provider, model

    # -- prompt shaping ---------------------------------------------------------
    @staticmethod
    def _compose_prompt(request: GenerationRequest, shot_prompt: str) -> str:
        """Identity first, then the shot. Order matters to most image/video models —
        the subject description should not be trailing context."""
        parts: list[str] = []
        if request.identity:
            fragment = request.identity.prompt_fragment()
            if fragment:
                parts.append(fragment)
        parts.append(shot_prompt)
        return ". ".join(p.strip(". ") for p in parts if p.strip())

    # -- resolution -------------------------------------------------------------
    def _resolve(self, request: GenerationRequest) -> list[tuple[PlannedShot, Any]]:
        """Every shot, resolved to its wire payload, paired with the provider that runs it.

        The single source of truth for "what would be sent". `plan()` drops the provider
        objects and shows the rest; `generate()` keeps them and submits. There is no second
        implementation, which is the only way a preview can be trusted to describe the run
        that a button actually buys — the same argument `services/kits.estimate_cents`
        makes about the price, applied to the payload the price is for.

        A shot that cannot run is still returned, carrying `skipped_reason`. Dropping it
        silently is how a brief for four videos quietly becomes a kit of two.
        """
        table = provider_table.resolve(self._backend)
        identity_negatives = request.identity.negatives if request.identity else []
        resolved: list[tuple[PlannedShot, Any]] = []

        for index, shot in enumerate(request.shots):
            # Identity first, then the shot's own. De-duplicated preserving order so a
            # term named in both does not reach the provider twice.
            seen: dict[str, None] = {}
            for term in [*identity_negatives, *shot.negatives]:
                term = term.strip()
                if term:
                    seen.setdefault(term, None)
            negatives = ", ".join(seen)

            provider, model = self._resolve_model(table, shot)
            rendered_seconds = provider_table.snap_duration(model, shot.seconds)

            params: dict[str, Any] = {}
            if rendered_seconds:
                params["duration"] = rendered_seconds
            if shot.modality is not Modality.AUDIO:
                params["aspect_ratio"] = shot.aspect_ratio

            if provider is None:
                reason = f"no {shot.modality.value} provider is credentialed"
            elif not model:
                reason = f"no model known for {shot.modality.value} on {type(provider).__name__}"
            else:
                reason = None

            plates, plate_note = self._plates_for(shot, request, provider)

            planned = PlannedShot(
                index=index,
                modality=shot.modality,
                label=shot.label,
                provider=type(provider).__name__ if provider is not None else None,
                vendor=provider_table.vendor_of(provider) if provider is not None else "none",
                model=model,
                prompt=self._compose_prompt(request, shot.prompt),
                negative_prompt=negatives,
                params=params,
                requested_seconds=shot.seconds,
                rendered_seconds=rendered_seconds,
                reference_keys=[plate.key for plate in plates],
                plate_note=plate_note,
                # Priced on the length that will be *rendered*, not the one asked for —
                # otherwise the quote is for a clip the provider will not make.
                estimate_cents=pricing.estimate_cents(shot.modality, model, rendered_seconds),
                skipped_reason=reason,
            )
            # A locked clip receives the still via `input_from`, which Genblaze validates
            # under the same rule as a plate — so a provider that refuses images refuses
            # the still too, and building one anyway would render and bill a frame whose
            # only consumer then kills the run.
            if shot.identity_lock and planned.runs and self._accepts_images(provider)[0]:
                planned.prelude, prelude_provider = self._identity_still(
                    shot, request, table, index
                )
                if planned.prelude is not None:
                    # The still is where the face is settled, so the clip does not also
                    # need to be handed the raw plates — it is conditioned on the still,
                    # which already is the face. Sending both would put two different
                    # images in the same positional slots and ask for an interpolation.
                    planned.reference_keys = []
                    planned.plate_note = (
                        f"locked by a still first — {planned.prelude.plate_note}"
                        if planned.prelude.plate_note
                        else "locked by a still first"
                    )
                    resolved.append((planned.prelude, prelude_provider))

            resolved.append((planned, provider))

        return resolved

    def _identity_still(self, shot: Any, request: GenerationRequest, table: dict, index: int):
        """The stage-one plate: this artist, in this scene, as a single frame.

        Returns `(None, None)` when there is nothing to lock — no image provider, no
        identity, no frames. That is a degradation rather than a refusal: a shot that
        wanted an identity lock and cannot have one is still a shot, and the plan says so
        on the row rather than failing the kit.

        The prompt is the clip's prompt with the motion language removed. A still rendered
        from "vertical 9:16 loop … steady mid-tempo movement at 96 BPM" is being asked for
        a frame of a video, and image models answer that with motion blur.
        """
        image_provider = table.get(Modality.IMAGE)
        if image_provider is None:
            return None, None

        model = (
            self._models.get(Modality.IMAGE)
            or provider_table.default_model(image_provider, Modality.IMAGE)
        )
        if not model:
            return None, None

        # A still may carry more than one reference — the positional-slot constraint that
        # limits a video shot to one is a fact about `first_frame`/`last_frame`, not about
        # image conditioning. How many actually reach the wire depends on the slot the
        # provider declares; see `_plate_assets`.
        plates, note = self._plates_for(shot, request, image_provider, limit=self._reference_limit)
        if not plates:
            return None, None

        prompt = self._still_prompt(shot.prompt)
        return (
            PlannedShot(
                index=index,
                modality=Modality.IMAGE,
                label=f"identity lock · {shot.label}" if shot.label else "identity lock",
                provider=type(image_provider).__name__,
                vendor=provider_table.vendor_of(image_provider),
                model=model,
                prompt=prompt,
                negative_prompt=", ".join(request.identity.negatives) if request.identity else "",
                params={"aspect_ratio": shot.aspect_ratio},
                reference_keys=[plate.key for plate in plates],
                plate_note=note,
                is_prelude=True,
                estimate_cents=pricing.estimate_cents(Modality.IMAGE, model, None),
            ),
            image_provider,
        )

    @staticmethod
    def _still_prompt(clip_prompt: str) -> str:
        """The clip's prompt, asked for as a photograph rather than as footage.

        Only the clauses that describe *motion or duration* are dropped. Everything about
        the subject, the light and the framing is what the still exists to settle, so it
        stays — the two stages must agree about the scene or the clip's first frame will
        be a different place than the rest of it.
        """
        motion_words = ("loop", "movement", "bpm", "silent footage", "handheld", "motion")
        kept = [
            clause
            for clause in clip_prompt.split(". ")
            if not any(word in clause.lower() for word in motion_words)
        ]
        body = ". ".join(kept) or clip_prompt
        return (
            f"Single still photograph, vertical 9:16. {body}. "
            "Sharp focus on the subject's face, no motion blur."
        )

    # -- the face --------------------------------------------------------------
    # How many conditioning images a video shot may carry. One, and the number is a
    # correctness constraint rather than a budget: Seedance routes a second image into
    # `last_frame`, which the model reads as "end the clip here" and interpolates towards.
    # A second reference frame would not strengthen the likeness, it would request a morph.
    PLATE_LIMIT = 1

    @property
    def _reference_limit(self) -> int:
        """How many references a *still* may carry. One unless a slot is configured.

        This is the honest boundary of what can be built without the vendor's docs in
        hand, and it is worth stating exactly rather than papering over.

        Genblaze can route any number of images — `route_images(array_slot=…)` exists and
        works. What it cannot know is the *key* a given model expects them under, and GMI
        ships no per-model payload schema in the package: every image model resolves
        through a permissive fallback declaring `route_images(slots=("image",))`, a single
        positional slot which silently drops the rest ("If `array_slot` is None, extras are
        dropped").

        So one reference is the only count this code can claim to have verified. A slot
        name invented here would either 400 or, far worse, be ignored — a kit that reports
        four references, is billed for one, and looks conditioned. `RK_REFERENCE_SLOT` is
        how an operator turns it on *after* checking the model's actual payload against
        GMI's catalogue, and `_plate_assets` records which slot was used in the manifest
        so a wrong guess is visible in the record rather than only in the output.
        """
        return self._reference_max if self._reference_slot else 1

    @staticmethod
    def _accepts_images(provider: Any) -> tuple[bool, str | None]:
        """Will this provider take an image input at all — from anywhere.

        One question, because Genblaze treats it as one: `_check_step_capabilities` sets
        `receives_chain` for `external_inputs`, `input_from`, *and* chain mode alike, and
        raises `GenblazeError` for any of them if the provider has not declared
        `accepts_chain_input`. So "can I hand it a plate" and "can I hand it the still from
        the previous step" have the same answer, and asking them separately is how the
        two-stage path grew a hole the single-stage path had already closed:
        `VeoProvider` refuses both, and the still would have been rendered, billed, and
        then killed the run it was rendered for.

        `None` capabilities means unspecified, which Genblaze documents as "no
        restriction" — permissive here rather than a refusal, and it is also what keeps
        the mock path exercising this code rather than routing around it.
        """
        caps = provider.get_capabilities() if hasattr(provider, "get_capabilities") else None
        if caps is None:
            return True, None
        name = type(provider).__name__
        if not getattr(caps, "accepts_chain_input", False):
            return False, f"{name} does not accept image inputs — the face is not sent"
        inputs = getattr(caps, "supported_inputs", None)
        if inputs and "image" not in inputs:
            return False, f"{name} takes {', '.join(inputs)} only — the face is not sent"
        return True, None

    def _plates_for(
        self, shot: Any, request: GenerationRequest, provider: Any, *, limit: int | None = None
    ):
        """The artist's face, if this shot wants it and this provider will take it.

        The gate is the provider's own declared capability, because getting it wrong is not
        a degraded render — it is a `GenblazeError` raised in `_validate_steps()` before any
        step runs, which fails the *whole* kit. And the providers genuinely disagree:
        `GMICloudVideoProvider` and `SoraProvider` declare `accepts_chain_input=True` with
        `image` among their inputs, while `VeoProvider` and `ImagenProvider` declare neither.
        So the same brief that conditions correctly on GMI would refuse outright on Google,
        and provider selection is a runtime decision nobody makes per kit.

        `None` capabilities means unspecified, which Genblaze documents as "no restriction"
        — so it is treated as permissive here rather than as a refusal. That is also what
        keeps the mock path exercising this code rather than routing around it.

        Returns the chosen frames and a note saying what happened, which is always set:
        a plate silently not sent looks exactly like no identity at all.
        """
        if not shot.use_identity_plate:
            return [], None
        if provider is None:
            return [], None

        identity = request.identity
        if identity is None:
            return [], "no identity — this shot will not hold a face"
        plates = identity.plates(limit=limit or GenblazeGenerator.PLATE_LIMIT)
        if not plates:
            return [], "identity has no reference frames — add one to hold the face"

        # A conditioning image is fetched *by the provider*, from its own network. The
        # local adapter's `/files/…` is an app route rather than an address, so on a live
        # backend it is a URL nothing can resolve — and this is precisely the class of bug
        # that passes on a laptop and fails in Batch. The mock generator fetches nothing,
        # so it is left alone: refusing there would mean the plate path is never exercised
        # by the thing the laptop actually runs.
        if self._backend != "mock" and not getattr(self._storage, "serves_public_urls", True):
            return [], (
                "local storage serves app-relative URLs that a provider cannot fetch — "
                "set RK_STORAGE_BACKEND=b2 to condition on the face"
            )

        accepts, refusal = self._accepts_images(provider)
        if not accepts:
            return [], refusal

        plate = plates[0]
        if len(plates) > 1:
            classes = ", ".join(p.angle.value for p in plates)
            note = f"identity v{identity.version} — {len(plates)} references ({classes})"
        else:
            where = f" ({plate.angle.value}, {plate.lighting})" if plate.lighting else ""
            note = f"identity v{identity.version}{where}"
        if not plate.sha256:
            # Not a refusal — it still conditions. But an unhashed input means Genblaze
            # keys the cache and the manifest off a presigned URL that rotates, so the
            # provenance record moves under a kit that has not changed.
            note += " — no content hash, so the manifest hash will not be stable"
        return plates, note

    def _plate_assets(self, keys: list[str], identity: Any) -> list[Any]:
        """Turn stored plate keys into fetchable Genblaze assets.

        The URL is presigned at submission time and never persisted, for the same reason
        `ui.routes.asset_url` derives its URLs at render time: a stored presigned URL is a
        link that works in the demo and is broken by the time anybody checks.

        `sha256` travels with it whenever the frame has one. Without it Genblaze falls back
        to hashing the URL, and a rotating URL means the step cache never hits and the
        manifest's canonical hash changes on every render of an unchanged kit.

        A plate that cannot be presigned is dropped rather than raised on. A broken
        reference image should cost a kit its likeness conditioning, not its whole run.
        """
        if not keys or identity is None:
            return []

        from genblaze_core.models.asset import Asset as GBAsset

        by_key = {frame.key: frame for frame in identity.reference_frames}
        assets: list[Any] = []
        for key in keys:
            try:
                url = self._storage.presign_get(key, expires_in=3600)
            except Exception as exc:
                log.warning("could not presign plate %s (%s) — sending without it", key, exc)
                continue
            frame = by_key.get(key)
            assets.append(
                GBAsset(
                    url=url,
                    media_type=getattr(frame, "content_type", None) or "image/png",
                    sha256=getattr(frame, "sha256", None),
                )
            )
        return assets

    def plan(self, request: GenerationRequest) -> GenerationPlan:
        """What this run would send, and what it would cost. Spends nothing."""
        resolved = self._resolve(request)
        # Preludes are nested on the shot they lock, not listed beside it — see
        # `PlannedShot.is_prelude`. Listing both would double the count and the quote.
        plan = GenerationPlan(shots=[p for p, _ in resolved if not p.is_prelude])

        if not plan.runnable:
            missing = sorted({s.modality.value for s in plan.skipped})
            plan.blocker = (
                "No provider available for any requested modality "
                f"({', '.join(missing)})." if missing
                else "This brief produces no shots."
            )
        elif self._max_run_cents and plan.estimate_cents > self._max_run_cents:
            plan.blocker = (
                f"Estimated {plan.estimate_cents}¢ exceeds the {self._max_run_cents}¢ "
                f"ceiling for a single run (RK_MAX_RUN_CENTS)."
            )
        return plan

    # -- the run ----------------------------------------------------------------
    def generate(self, request: GenerationRequest) -> GenerationResult:
        from genblaze_core import Pipeline
        from genblaze_core.models.enums import Modality as GBModality

        table = provider_table.resolve(self._backend)
        if not table:
            return GenerationResult(
                run_id=None,
                assets=[],
                error="No generation providers are available. Install a Genblaze "
                "provider extra and set its API key, or run with RK_GENERATOR_BACKEND=mock.",
            )

        # The same resolution the preview screen showed. Not a re-derivation of it.
        resolved = self._resolve(request)

        pipeline = Pipeline(
            f"kit:{request.kit_id}",
            tenant_id=request.tenant_id,
            max_concurrency=self._max_concurrency,
            # `preflight=True` is Genblaze's default and it bills real money on GMI.
            # The probe is not a metadata lookup — `genblaze_gmicloud/_probe.py` does:
            #
            #     resp = http.post("/requests", json={"model": slug, "payload": {}})
            #
            # a real generation request with an empty payload, which GMI queues, runs
            # from its own defaults, and charges for at the full model rate. The adapter
            # discards the outcome ("the probe verdict is LIVE either way").
            #
            # On 2026-07-31 this was half the bill: of four `seedance-2` units at $0.61,
            # two were promptless probes — one of which completed and produced a video of
            # a woman in an autumn park that nobody asked for. Each `.run()` would pay
            # this again, per video model, forever.
            #
            # Turning it off is safe here because the thing preflight checks is already
            # checked: `providers.DEFAULT_MODELS` pins a known-good model per provider,
            # and `tests/test_providers` asserts every resolvable provider has one.
            preflight=False,
        )

        planned: list[tuple[Modality, str, str, float | None]] = []  # + seconds
        skipped: list[str] = []
        # Where each identity-locking still landed in the pipeline, so the clip that
        # follows it can name it as its input. Keyed by the `ShotSpec` index both stages
        # share.
        still_at: dict[int, int] = {}
        step_index = 0

        for shot, provider in resolved:
            if not shot.runs:
                skipped.append(shot.modality.value)
                log.warning("skipping %s shot — %s", shot.modality.value, shot.skipped_reason)
                continue

            model, prompt = shot.model, shot.prompt
            # The brief asks for a musically-derived length; the model has its own list of
            # lengths that exist. Sora takes only {4, 8, 12}, the GMI models take any
            # integer 1..60, and `briefs._loop_seconds` hands over a float like 9.3 — so
            # an unsnapped request is rejected outright ("Invalid seconds=10"). Snapping
            # happened in `_resolve`, which is why the preview could show the real length.
            params = dict(shot.params)
            rendered_seconds = shot.rendered_seconds

            kwargs: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "modality": getattr(GBModality, _MODALITY_MAP[shot.modality]),
                "metadata": {
                    "kit_id": request.kit_id,
                    "artist": request.artist_name,
                    "song": request.song.title,
                    # The hook window travels into every manifest, so the one free
                    # lever is recorded provenance rather than a lost parameter.
                    "hook_start_ms": request.song.hook.start_ms,
                    "hook_end_ms": request.song.hook.end_ms,
                    "bpm": request.song.bpm,
                    # What the brief asked for versus what the model could render. A kit
                    # that claims it was cut to a 10s hook while its clips are 8s would be
                    # the provenance rule quietly defeated by a provider constraint, so
                    # both numbers travel into the manifest.
                    "requested_seconds": shot.requested_seconds,
                    "rendered_seconds": rendered_seconds,
                },
            }
            if params:
                kwargs["params"] = params
            if shot.negative_prompt:
                kwargs["negative_prompt"] = shot.negative_prompt

            # The face, as an actual image the provider fetches. Minted here rather than in
            # `_resolve` because a presigned URL is a short-lived credential and the preview
            # screen must not be in the business of issuing them — `plan()` runs on every
            # keystroke of a re-price, and none of those runs is going to generate anything.
            # Where this step's images come from. Exactly one of the two, never both: a
            # clip locked by a still is conditioned on the still, and adding the raw plates
            # alongside it would put two different images in the same positional slots and
            # ask the model to interpolate between them.
            feeding_still = still_at.get(shot.index)
            if feeding_still is not None and not shot.is_prelude:
                kwargs["input_from"] = feeding_still
            else:
                inputs = self._plate_assets(shot.reference_keys, request.identity)
                if inputs:
                    kwargs["external_inputs"] = inputs
                    if len(inputs) > 1 and self._reference_slot:
                        # Which key the extra references travelled under, recorded in the
                        # manifest. If the slot turns out to be wrong for this model the
                        # evidence is in the run's own record rather than only in a face
                        # that came back looking like somebody else.
                        kwargs["metadata"]["reference_slot"] = self._reference_slot
                        kwargs["metadata"]["reference_count"] = len(inputs)

            pipeline = pipeline.step(provider, **kwargs)
            if shot.is_prelude:
                still_at[shot.index] = step_index
            step_index += 1
            planned.append((shot.modality, model, prompt, rendered_seconds))

        if not planned:
            return GenerationResult(
                run_id=None,
                assets=[],
                error=f"No provider available for any requested modality ({', '.join(sorted(set(skipped)))}).",
            )

        # The spend ceiling, applied before anything is submitted.
        #
        # This is the guard the incident on 2026-07-31 did not have. A fallback chain
        # across four video models was submitted from one call; two steps failed, produced
        # no asset, and were billed anyway. Nothing in Genblaze stops that — its README
        # documents no budget or spend guardrail, and a failed step's billing behaviour is
        # not specified at all. So the only safe assumption is that a *submitted* step is a
        # charged step, and the only safe place to refuse is before submission.
        estimate = pipeline.estimated_cost()
        estimate_cents = int(round(float(estimate) * 100)) if estimate is not None else None
        if estimate_cents is None:
            # Unpriced means unknown, and unknown must not price as free — that is exactly
            # how the incident stayed invisible in the ledger.
            estimate_cents = sum(
                pricing.estimate_cents(modality, model, seconds)
                for modality, model, _prompt, seconds in planned
            )
        if self._max_run_cents and estimate_cents > self._max_run_cents:
            return GenerationResult(
                run_id=None,
                assets=[],
                error=(
                    f"Refusing to run: estimated {estimate_cents}¢ exceeds the "
                    f"{self._max_run_cents}¢ ceiling for a single run "
                    f"(RK_MAX_RUN_CENTS). {len(planned)} shot(s) planned."
                ),
            )
        log.info(
            "kit %s: %d shot(s), estimated %d¢ (ceiling %s¢)",
            request.kit_id,
            len(planned),
            estimate_cents,
            self._max_run_cents or "none",
        )

        sink = self._build_sink()
        try:
            result = pipeline.run(
                sink=sink,
                timeout=self._timeout,
                raise_on_failure=False,
                progress=False,
            )
        except Exception as exc:  # a provider blowing up is a failed kit, not a 500
            log.exception("kit %s: pipeline raised", request.kit_id)
            return GenerationResult(run_id=None, assets=[], error=str(exc))

        return self._collect(result, skipped)

    # -- result mapping ---------------------------------------------------------
    def _collect(self, result: Any, skipped: list[str]) -> GenerationResult:
        assets: list[Asset] = []

        for step in result.succeeded_steps():
            # `or 0.0` used to sit here and it was the whole bug: an unpriced model
            # recorded as free. None now travels as None so the console can say
            # "unknown" instead of "$0.00".
            model_id = str(step.model)
            cost_cents = (
                int(round(float(step.cost_usd) * 100)) if step.cost_usd is not None else None
            )
            cost_note = pricing.note_for(model_id)
            for gb_asset in step.assets or []:
                url = getattr(gb_asset, "url", None)
                assets.append(
                    Asset(
                        modality=Modality(str(step.modality)),
                        provider=str(step.provider),
                        model=str(step.model),
                        prompt=step.prompt,
                        key=self._storage.key_from_url(url) if url else None,
                        url=url,
                        sha256=getattr(gb_asset, "sha256", None),
                        cost_cents=cost_cents,
                        cost_note=cost_note,
                        params=dict(step.params or {}),
                    )
                )

        manifest = result.manifest
        try:
            verified = bool(manifest.verify())
        except Exception as exc:
            log.warning("manifest verification raised: %s", exc)
            verified = False

        error = result.error_summary()
        if skipped:
            note = f"No provider for modality: {', '.join(sorted(set(skipped)))}"
            error = f"{error}\n{note}" if error else note

        manifest_uri = getattr(manifest, "manifest_uri", None)
        return GenerationResult(
            run_id=str(result.run.run_id),
            assets=assets,
            manifest_key=self._storage.key_from_url(manifest_uri) if manifest_uri else None,
            manifest_verified=verified,
            error=error,
        )
