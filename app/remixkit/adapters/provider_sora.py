"""Sora's `input_reference`, in the shape and the transport the API actually documents.

This took three rejections to pin down, and each one refuted the fix before it. Worth
recording in order, because the lesson is that "what" and "how" are separate questions and
answering one does not answer the other.

The connector uploads the start frame as a file. The server refused:

    Invalid type for 'input_reference': expected an object, but got a file instead.

So it wants an object — `{"image_url": …}`. Sending one through `client.videos.create()`
was then refused by the SDK, before any request went out:

    Expected entry at `input_reference` to be bytes, an io.IOBase instance, PathLike or a
    tuple but received <class 'dict'> instead.

Which reads like a contradiction and is not. `Videos.create()` unconditionally sets
`Content-Type: multipart/form-data` and unconditionally runs
`extract_files(body, paths=[["input_reference"]])`, which *pops* the value and asserts it
is file content. The generated method only implements the file half of a parameter its own
type declares as a union — `Union[FileTypes, ImageInputReferenceParam]`. So the typed
method cannot express the object, and the union is not a choice it offers.

The spec settles the rest. From the OpenAPI document the SDK is generated from
(`.stats.yml` → `CreateVideoMultipartBody`), `POST /v1/videos` declares exactly one request
content type, `multipart/form-data`, and:

    input_reference:
      anyOf:
        - type: string, format: binary
        - $ref: ImageRefParam   # "Provide exactly one of `image_url` or `file_id`"

`ImageRefParam.image_url` is *"A fully qualified URL or base64-encoded data URL"* with
`maxLength: 20971520`.

So JSON was never on the table — the object is a **multipart field**, not a JSON body. The
SDK's own serializer flattens nested values with brackets, so the field is
`input_reference[image_url]`, which is what this module sends. That encoding is not
invented here: it is what `BaseClient._serialize_multipartform` produces from the same
spec, and the test builds the real request and reads the field name back off the wire.

Only `submit` is overridden, and only this parameter within it. `prepare_payload`'s
validation and SSRF checks, seconds and size handling, error mapping, discovery, polling
and download are all inherited, because this is a correction and not a second
implementation. A shot with no reference never leaves the connector's own typed path. When
`videos.create()` learns to send the object arm, deleting this module should be the whole
of the change.

Two forms of reference arrive here and they are not interchangeable:

*   An **https:// URL**, which is what a plate is. `_plate_assets` presigns it out of B2
    and it is already the address a provider is expected to fetch from, so it goes over as
    `image_url` untouched. The connector downloaded it first and re-uploaded the bytes; for
    a user's photograph that was megabytes of round trip to arrive at a string.
*   A **file:// path**, which is what a chained still is — the previous step's output
    before the sink has uploaded it anywhere. There is no address to give, so the bytes are
    inlined as a data URL, resolved through the connector's own allowlist check rather than
    opened directly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# `ImageRefParam.image_url`, `maxLength: 20971520`. Checked here rather than left to the
# vendor because a data URL is the one input that can plausibly reach it — base64 costs
# about a third on top of a photograph that was already large — and a refusal naming the
# frame is worth more than a 400 naming a field.
MAX_IMAGE_URL = 20_971_520


def sora_provider_class() -> Any:
    """The corrected `SoraProvider`, or the connector's own if the seam has moved.

    Imported inside the function because the whole OpenAI extra is optional — a deployment
    without it must not pay an ImportError at module scope — and built defensively because
    some of what it reaches for is the connector's private surface. If that has been
    rearranged, the shipped provider is returned unchanged: a wrong `input_reference` is a
    400 on the video step, where an exception raised while *building the class* takes out
    every provider in the table, including the ones that were working.
    """
    try:
        from genblaze_core.exceptions import ProviderError
        from genblaze_core.models.enums import ProviderErrorCode
        from genblaze_core.providers.retry import retry_after_from_response
        from genblaze_openai import SoraProvider as _SoraProvider
        from genblaze_openai._errors import map_openai_error
        from genblaze_openai.dalle import _resolve_local_file
        from openai.types.video import Video
    except Exception as exc:  # pragma: no cover — depends on the installed connector
        log.warning("could not correct Sora's input_reference (%s)", exc)
        from genblaze_openai import SoraProvider as _SoraProvider

        return _SoraProvider

    class SoraProvider(_SoraProvider):  # type: ignore[misc, valid-type]
        """`SoraProvider` with the start frame sent as an object rather than an upload."""

        def _input_reference(self, image_url: str | None) -> dict[str, str] | None:
            """The `ImageRefParam` for this asset, or `None` if the shot has no reference.

            A missing reference is not an error here. Sora renders unlocked shots too, and
            the refusal for a shot that *needed* a face belongs on the plan screen where
            somebody can act on it — not at submit time with the run half spent.
            """
            if not image_url:
                return None

            if urlparse(image_url).scheme == "file":
                path = _resolve_local_file(image_url, self._output_dir)
                media_type = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                image_url = f"data:{media_type};base64,{encoded}"
                if len(image_url) > MAX_IMAGE_URL:
                    raise ProviderError(
                        f"reference frame is {len(image_url) // 1024}KiB as a data URL, "
                        f"over the {MAX_IMAGE_URL // 1024}KiB the Videos API accepts",
                        error_code=ProviderErrorCode.INVALID_INPUT,
                    )
                return {"image_url": image_url}

            # http(s). `prepare_payload` has already SSRF-validated it, and the fetch is
            # OpenAI's to make from here.
            return {"image_url": image_url}

        def _create_with_reference(self, body: dict[str, Any]) -> Any:
            """POST /v1/videos as multipart, with the reference as a nested field.

            The typed `videos.create()` cannot carry the object arm — it pops
            `input_reference` into the file list and asserts it is file content — so the
            request is issued one level down, through the same client. Auth, base URL,
            retries, timeouts and response parsing are all still the SDK's; the only thing
            being said differently is the body.

            `files=[]` with an explicit multipart content type is what routes the body
            through `_serialize_multipartform`, which brackets nested values into
            `input_reference[image_url]`.
            """
            return self._get_client().post(
                "/videos",
                body=body,
                files=[],
                cast_to=Video,
                options={"headers": {"Content-Type": "multipart/form-data"}},
            )

        def submit(self, step: Any, config: Any = None) -> Any:
            """Create the video job. The connector's `submit`, with the reference fixed."""
            try:
                payload = self.prepare_payload(step)

                body: dict[str, Any] = {
                    "model": step.model,
                    "prompt": payload.get("prompt", step.prompt or ""),
                }
                for key in ("seconds", "size"):
                    if key in payload:
                        body[key] = payload[key]
                if "seconds" in body:
                    # The SDK's VideoSeconds is Literal["4", "8", "12"] — not int.
                    body["seconds"] = str(body["seconds"])

                reference = self._input_reference(payload.get("image"))
                if reference is None:
                    # Nothing to correct — stay on the supported typed path.
                    return self._get_client().videos.create(**body).id

                body["input_reference"] = reference
                return self._create_with_reference(body).id
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(
                    f"Sora submit failed: {exc}",
                    error_code=map_openai_error(exc),
                    retry_after=retry_after_from_response(exc),
                ) from exc

    return SoraProvider
