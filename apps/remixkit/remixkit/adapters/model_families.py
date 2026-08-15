"""Payload shapes the connector does not ship, learned from what the API actually said.

Genblaze resolves a model to a `ModelFamily` and each family declares how input assets are
routed onto the wire. Models with no matching family fall through to a permissive fallback
that emits `{"image": <url>}`, and when the vendor wants something else the request is
rejected — or, worse, accepted and ignored.

Every entry here is **evidence, not inference**. That distinction is the whole reason this
module exists: two earlier attempts at the same problem guessed a slug and a slot name from
the connector's `example_slugs`, and both failed in production. A rejection that names the
missing parameter is the one source that cannot be wrong about it.

    bria-fibo-edit → 400: invalid payload parameters: images (Required parameter is missing)

So Bria's fibo family takes `images`, plural, as an array. The connector ships a family for
`bria-genfill` and `bria-eraser` only; everything else Bria publishes lands on the fallback
with the singular key.

The array's *length* is a separate fact, and reading it off the plural is the mistake that
cost the run after this module was written. `images` was taken as "several references at
last", the classed set went out four frames wide, and the vendor answered:

    bria-fibo-edit → 400: invalid payload parameters: images
    (Number of images 4 exceeds maximum allowed value 1)

`maxItems: 1`. A container and its capacity are two things the API states separately, and
only one of them was ever stated here. So the family declares the count alongside the key
— both quoted, neither inferred — and the mapper truncates to it, because a payload built
here is the last place the request can still be made valid.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def _capped(mapping: Any, limit: int, slot: str) -> Any:
    """Route at most `limit` assets, because the vendor's array has a `maxItems`.

    Truncating silently is normally the failure this codebase refuses — a kit that reports
    four references and sends one looks conditioned and is not. It is right here only
    because the count is enforced twice and the *visible* one comes first:
    `providers.REFERENCE_LIMITS` holds the same number, so the generator renders one plate,
    prices one, and writes one to the plan row. Nothing upstream believes in a frame this
    drops.

    What this catches is the caller that has not asked — a chained step, a retry, a future
    path that hands over whatever it happens to be holding. Against those the choice is a
    valid request or a 400 after the money is spent, and the log line keeps the trim in the
    record rather than only in the payload.
    """

    def route(assets: Any) -> Any:
        assets = list(assets or ())
        if len(assets) > limit:
            log.warning(
                "trimming %d assets to %d for `%s` — the vendor's maximum",
                len(assets), limit, slot,
            )
            assets = assets[:limit]
        return mapping(assets)

    return route


def _bria_fibo_family() -> Any:
    from genblaze_core.models.enums import Modality
    from genblaze_core.providers import ModelFamily, ModelSpec, ParamSurface, route_images

    # `images` has to be on the *allowlist* as well as produced by the input mapping.
    #
    # This is the step that made the previous fix a no-op, and it fails in the most
    # deniable way available: the mapper inserts the key, the allowlist filter removes it
    # one line later, and the only trace is a log line —
    #
    #     Dropping non-allowlisted params for bria-fibo-edit: ['images']
    #
    # — after which the request goes out without the parameter the model requires and GMI
    # rejects it for exactly the reason it was rejected before the fix. Two runs failed
    # identically and I read that as a stale deployment rather than as the same bug.
    #
    # `ParamSurface.for_modality` builds the allowlist from the *universally meaningful*
    # params for a modality, which cannot include a vendor's input slot names. Anything a
    # family routes into the payload has to be extended onto its own surface.
    surface = (
        ParamSurface.for_modality(Modality.IMAGE)
        .extend("number_of_images", "images")
        # `prompt` → `instruction`.
        #
        # Bria's fibo models are *edit* models: they take pictures plus a statement of what
        # to change, and they reject a request carrying neither `instruction` nor
        # `structured_instruction`:
        #
        #     422: Either 'instruction' or 'structured_instruction' must be provided.
        #
        # The canonical name across every other provider here is `prompt`, so the rename
        # belongs on this family rather than in the caller — a shot should not have to know
        # which vendor is going to answer. `with_aliases` adds the target to the allowlist
        # automatically, which is the mistake made one commit ago in the other direction.
        .with_aliases(prompt="instruction")
    )
    return ModelFamily(
        name="rk-bria-fibo",
        # The fibo edit family plus the standalone utilities, all of which take an image.
        # `genfill`/`eraser` are excluded — the connector already ships a family for them
        # with a mask surface, and a user family is checked *first*, so matching them here
        # would override a more specific mapping with a less specific one.
        pattern=re.compile(r"^bria-(?!genfill$|eraser$)", re.IGNORECASE),
        spec_template=ModelSpec(
            model_id="*",
            modality=Modality.IMAGE,
            # An array of one. The plural key and the single slot are both quoted from
            # rejections — see the module docstring — and the second does not follow from
            # the first, which is exactly how the fix that only had the first went out.
            input_mapping=_capped(route_images(array_slot="images"), 1, "images"),
            extras={"envelope_key": "payload"},
            **surface.build(),
        ),
        description="Bria fibo edit family — takes `images` as an array, per GMI's own 400.",
        example_slugs=("bria-fibo-edit", "bria-fibo-restyle", "bria-fibo-relight"),
    )


def register(registry: Any) -> Any:
    """Prepend the families this app has evidence for. Returns the registry it was given.

    User families are checked before the connector's own, which is what lets a payload
    shape be corrected without forking the package. Registration failures are logged and
    swallowed: a connector that changes this API should degrade to the shipped behaviour
    rather than take the process down at import time.
    """
    for build in (_bria_fibo_family,):
        try:
            registry.register_family(build())
        except Exception as exc:
            log.warning("could not register model family (%s)", exc)
    return registry
