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

That array is also the answer `RK_REFERENCE_SLOT` was waiting for on this model — a slot
that takes a list is a slot that takes *several references*, which is what a likeness needs
and what a duo needs. It is recorded here rather than configured because, unlike a model
slug, this one has been confirmed by the vendor.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def _bria_fibo_family() -> Any:
    from genblaze_core.models.enums import Modality
    from genblaze_core.providers import ModelFamily, ModelSpec, ParamSurface, route_images

    surface = ParamSurface.for_modality(Modality.IMAGE).extend("number_of_images")
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
            input_mapping=route_images(array_slot="images"),
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
