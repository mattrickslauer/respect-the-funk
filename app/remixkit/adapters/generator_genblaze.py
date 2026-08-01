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
    ) -> None:
        self.name = f"genblaze:{backend}"
        self._storage = storage
        self._backend = backend
        self._prefix = prefix
        self._models = models or {}
        self._timeout = timeout_s
        self._max_concurrency = max_concurrency
        self._max_run_cents = max_run_cents

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
                # Priced on the length that will be *rendered*, not the one asked for —
                # otherwise the quote is for a clip the provider will not make.
                estimate_cents=pricing.estimate_cents(shot.modality, model, rendered_seconds),
                skipped_reason=reason,
            )
            resolved.append((planned, provider))

        return resolved

    def plan(self, request: GenerationRequest) -> GenerationPlan:
        """What this run would send, and what it would cost. Spends nothing."""
        resolved = self._resolve(request)
        plan = GenerationPlan(shots=[planned for planned, _ in resolved])

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

            pipeline = pipeline.step(provider, **kwargs)
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
