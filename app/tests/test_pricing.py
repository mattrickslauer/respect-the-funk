"""What a shot costs, and the two ways that number used to lie.

These are regression guards on a live incident (2026-07-31). A validation run against
GMI Cloud spent real money while the console's ledger read `$0.00` and the pre-flight
estimate read `$1.26`. Neither figure was connected to the invoice:

* Genblaze registers **no prices of its own** — "the SDK ships zero hardcoded prices as
  of 0.3.0" — so `step.cost_usd` is `None` for every unregistered model, and the
  generator coerced that to `0`.
* `services/kits` carried a separate flat table that priced a *modality* rather than a
  *model*, and ignored duration, so every video cost 42¢ regardless of which model ran
  or how long the clip was.

Both are now one registered book (`adapters/pricing`), and these tests hold it there.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from remixkit.adapters import pricing
from remixkit.domain.models import Asset, Kit, Modality


# ------------------------------------------------------------------ unknown != free
def test_an_unpriced_model_does_not_estimate_as_free():
    """The core of the incident. A model nobody has priced must trip the budget gate,
    not slide under it at zero."""
    cents = pricing.estimate_cents(Modality.VIDEO, "some-model-nobody-priced", 6.0)
    assert cents == int(pricing.UNKNOWN_CALL_USD * 100)
    assert cents > 0


def test_an_absent_model_id_is_also_not_free():
    assert pricing.estimate_cents(Modality.VIDEO, None, 6.0) > 0


# ------------------------------------------------------------------ video scales with time
def test_video_is_priced_by_duration_not_per_call():
    """A flat per-video price is what made a 42¢ estimate survive a multi-dollar run."""
    six = pricing.estimate_cents(Modality.VIDEO, "sora-2", 6.0)
    twelve = pricing.estimate_cents(Modality.VIDEO, "sora-2", 12.0)
    assert twelve == pytest.approx(six * 2, rel=0.01)
    assert six > 0


def test_different_video_models_are_priced_differently():
    """The old table gave every video the same price. Seedance-2 and the fast tier are
    not the same money, and the estimate has to know that."""
    expensive = pricing.estimate_cents(Modality.VIDEO, "seedance-2-0-260128", 6.0)
    cheap = pricing.estimate_cents(Modality.VIDEO, "seedance-1-0-pro-fast-251015", 6.0)
    assert expensive > cheap


def test_the_old_flat_rate_would_have_underquoted_seedance_two():
    """The specific miss, pinned. The retired table said 42¢ for any video."""
    assert pricing.estimate_cents(Modality.VIDEO, "seedance-2-0-260128", 6.0) > 42


# ------------------------------------------------------------------ honesty flags
def test_measured_but_unreconciled_rates_are_marked_unverified():
    """A rate nobody has checked against an invoice must not read like a measured one."""
    assert pricing.UNVERIFIED, "the honesty flag is pointless if nothing is ever flagged"
    for model_id in pricing.UNVERIFIED:
        assert not pricing.is_verified(model_id)


def test_an_invoice_reconciled_rate_is_verified():
    """`seedance-2` is the one rate read off a real bill: 4 units, $2.44, $0.61 per 6s."""
    assert pricing.is_verified("seedance-2-0-260128")
    assert "invoice-reconciled" in pricing.note_for("seedance-2-0-260128")
    # 6 seconds at the reconciled per-second rate is the observed per-call charge.
    assert pricing.estimate_cents(Modality.VIDEO, "seedance-2-0-260128", 6.0) == 61


# ------------------------------------------------------------------ the audio trap
def test_models_that_generate_audio_are_named():
    """The product wants silent video — the loop is cut over the label's own master.

    `seedance-2` failed four times and billed in full because it invented an audio bed
    that its own filter then judged possibly copyright-infringing
    (`OutputAudioSensitiveContentDetected.PolicyViolation`). Knowing which models do that
    is a product fact, not trivia.
    """
    assert "seedance-2-0-260128" in pricing.MODELS_THAT_GENERATE_AUDIO
    assert "sora-2" in pricing.MODELS_THAT_GENERATE_AUDIO
    # Kling text-to-video emits no audio, which is why it is the safe default here.
    assert "Kling-Text2Video-V2.1-Master" not in pricing.MODELS_THAT_GENERATE_AUDIO


def test_every_audio_generating_model_is_priced():
    """These are the expensive ones to get wrong — an unpriced audio-generating video
    model is the exact combination that produced the incident."""
    for model_id in pricing.MODELS_THAT_GENERATE_AUDIO:
        assert model_id in pricing.PRICE_BOOK, model_id


def test_every_priced_model_carries_a_note():
    for model_id in pricing.PRICE_BOOK:
        assert pricing.note_for(model_id).strip(), model_id


def test_every_default_model_is_priced():
    """A provider the app will actually resolve must have a rate, or the budget gate is
    quoting `UNKNOWN_CALL_USD` for normal operation rather than for a surprise."""
    from remixkit.adapters.providers import DEFAULT_MODELS

    for provider_name, per_modality in DEFAULT_MODELS.items():
        for modality, model_id in per_modality.items():
            assert model_id in pricing.PRICE_BOOK, f"{provider_name} → {model_id} is unpriced"


# ------------------------------------------------------------------ the ledger
def test_unknown_asset_cost_is_none_not_zero():
    """`cost_cents=0` and `cost_cents=None` are different claims. The first says free."""
    kit = Kit(
        tenant_id="t",
        artist_id="a",
        song_id="s",
        name="k",
        assets=[
            Asset(modality=Modality.VIDEO, provider="p", model="m", cost_cents=None),
            Asset(modality=Modality.IMAGE, provider="p", model="m", cost_cents=19),
        ],
    )
    kit.recost()
    assert kit.total_cost_cents == 19  # the known part still totals
    assert kit.cost_is_complete is False  # but the total is a floor, not the invoice


def test_a_fully_priced_kit_reports_a_complete_cost():
    kit = Kit(
        tenant_id="t",
        artist_id="a",
        song_id="s",
        name="k",
        assets=[Asset(modality=Modality.IMAGE, provider="p", model="m", cost_cents=3)],
    )
    kit.recost()
    assert kit.cost_is_complete is True
    assert kit.total_cost_cents == 3


# ------------------------------------------------------------------ registration
def test_pricing_registers_onto_a_fork_not_the_shared_default():
    """Mutating `models_default()` would leak this app's price opinions into every other
    Genblaze consumer in the process."""
    gmicloud = pytest.importorskip("genblaze_gmicloud")
    provider_cls = gmicloud.GMICloudVideoProvider
    shared = provider_cls.models_default()
    forked = pricing.priced_registry(provider_cls)
    assert forked is not shared


def test_a_registered_rate_actually_prices_a_step():
    """The end of the chain: a registered strategy must produce a number Genblaze will
    put on the step, which is what makes `Pipeline.estimated_cost()` real."""
    strategy, _note = pricing.PRICE_BOOK["sora-2"]

    class _Ctx:
        step = type("S", (), {"params": {"duration": 10}})()
        output_count = 1
        output_duration_s = 10.0

    assert strategy(_Ctx()) == Decimal("1.0")


# ------------------------------------------------------------------ preflight
def test_the_pipeline_is_built_with_preflight_disabled():
    """Genblaze's preflight probe is a *billable generation request*, not a lookup.

    `genblaze_gmicloud/_probe.py` posts `{"model": slug, "payload": {}}` to `/requests`
    — a real submission GMI queues, runs from its own defaults, and charges for at the
    full model rate, whose outcome the adapter then discards. On 2026-07-31 it was half
    the bill: two of four `seedance-2` units at $0.61 were promptless probes, and one of
    them produced a video nobody asked for.

    This asserts on the source rather than by running a pipeline, because the cheapest
    possible regression test for "does this cost $0.61 every run" is one that never
    submits anything.
    """
    import inspect

    from remixkit.adapters import generator_genblaze

    src = inspect.getsource(generator_genblaze.GenblazeGenerator.generate)
    assert "preflight=False" in src, (
        "Pipeline must be constructed with preflight=False — the default probe bills "
        "a full generation per video model per run"
    )
