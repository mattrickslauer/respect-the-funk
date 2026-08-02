"""Sora's `input_reference`, in the shape the API accepts today.

The connector uploads the start frame as a file, which is what the Videos API used to
take. It is not what it takes now:

    Step 1 (openai-sora/sora-2): Sora submit failed: Error code: 400 -
    {'error': {'message': "Invalid type for 'input_reference': expected an object,
    but got a file instead.", 'type': 'invalid_request_error',
    'param': 'input_reference', 'code': 'invalid_type'}}

The installed SDK agrees with the rejection rather than with the connector. `openai`
2.52.0 types the parameter as a union — `Union[FileTypes, ImageInputReferenceParam]` —
where the object arm is `{"file_id": str} | {"image_url": str}` and `image_url` is
documented as *"A fully qualified URL or base64-encoded data URL."* The file arm is the
compatibility half of that union, and the server has stopped honouring it.

So this is the same class of correction as `model_families`, one layer up: the payload
shape a vendor wants, recorded from the vendor's own refusal, applied without forking the
package. `genblaze-openai` 0.3.4 is the latest release and still sends the file.

Only `submit` is overridden, and only the one parameter inside it. Everything the
connector does around that — `prepare_payload`'s validation and SSRF checks, the seconds
and size handling, the error mapping, discovery, polling, download — is inherited, because
this file is a correction and not a second implementation. When the connector ships the
object form, deleting this module should be the whole of the change.

Two forms of reference arrive here and they are not interchangeable:

*   An **https:// URL**, which is what a plate is. `_plate_assets` presigns it out of B2
    and it is already the address a provider is expected to fetch from, so it goes over as
    `image_url` untouched. The connector downloaded it first and re-uploaded the bytes; a
    URL the API will accept makes that round trip pure cost, and for a user's photograph
    it was megabytes of it.
*   A **file:// path**, which is what a chained still is — the previous step's output
    before the sink has uploaded it anywhere. There is no address to give, so the bytes
    are inlined as a data URL, resolved through the connector's own allowlist check rather
    than opened directly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def sora_provider_class() -> Any:
    """The corrected `SoraProvider`, or the connector's own if the seam has moved.

    Imported inside the function because the whole OpenAI extra is optional — a deployment
    without it must not pay an ImportError at module scope — and built defensively because
    the pieces reached for here are the connector's privates. If it has been rearranged,
    the shipped provider is returned unchanged: a wrong `input_reference` is a 400 on the
    video step, where an exception raised while *building the class* takes out every
    provider in the table, including the ones that were working.
    """
    try:
        from genblaze_core.exceptions import ProviderError
        from genblaze_openai import SoraProvider as _SoraProvider
        from genblaze_openai._errors import map_openai_error
        from genblaze_openai.dalle import _resolve_local_file
        from genblaze_core.providers.retry import retry_after_from_response
    except Exception as exc:  # pragma: no cover — depends on the installed connector
        log.warning("could not correct Sora's input_reference (%s)", exc)
        from genblaze_openai import SoraProvider as _SoraProvider

        return _SoraProvider

    class SoraProvider(_SoraProvider):  # type: ignore[misc, valid-type]
        """`SoraProvider` with the start frame sent as an object rather than a file."""

        def _input_reference(self, image_url: str | None) -> dict[str, str] | None:
            """The `input_reference` object for this asset, or `None` if there isn't one.

            A missing reference is not an error here. Sora is reached for unlocked shots
            too, and the refusal for a shot that *needed* a face belongs on the plan screen
            where somebody can act on it — not at submit time with the run half spent.
            """
            if not image_url:
                return None
            if urlparse(image_url).scheme == "file":
                path = _resolve_local_file(image_url, self._output_dir)
                media_type = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return {"image_url": f"data:{media_type};base64,{encoded}"}
            # http(s). `prepare_payload` has already SSRF-validated it, and the fetch is
            # OpenAI's to make from here.
            return {"image_url": image_url}

        def submit(self, step: Any, config: Any = None) -> Any:
            """Create the video job. Mirrors the connector's `submit`, minus the upload."""
            try:
                payload = self.prepare_payload(step)

                params: dict[str, Any] = {
                    "model": step.model,
                    "prompt": payload.get("prompt", step.prompt or ""),
                }
                for key in ("seconds", "size"):
                    if key in payload:
                        params[key] = payload[key]
                if "seconds" in params:
                    # The SDK's VideoSeconds is Literal["4", "8", "12"] — not int.
                    params["seconds"] = str(params["seconds"])

                reference = self._input_reference(payload.get("image"))
                if reference is not None:
                    params["input_reference"] = reference

                return self._get_client().videos.create(**params).id
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(
                    f"Sora submit failed: {exc}",
                    error_code=map_openai_error(exc),
                    retry_after=retry_after_from_response(exc),
                ) from exc

    return SoraProvider
