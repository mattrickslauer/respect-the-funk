"""Where a master lives, and the two signatures that keep it out of this process.

`app/remixkit/ports/storage.py` already made this argument for RemixKit and the shape is
reused here deliberately: a Protocol, one adapter behind it, and presigned URLs so the
browser talks to the bucket directly. What is **not** reused is the provider. `app/` uses
Backblaze B2 because the Backblaze sponsor track required it; that is a fact about that
codebase's history and not a pattern to inherit. The platform runs on AWS, so masters
live in S3 — which also makes S3 the second qualifying AWS service in the submission,
where today there is only Lambda.

## Why presigned, and not an upload endpoint

A master is 50–100 MB. Lambda's synchronous response payload cap is 6 MB and a Function
URL's is 20 MB (streaming aside), so an upload endpoint does not merely cost money and
wall-clock — it does not work at all above the cap, and the failure arrives as an opaque
`RequestEntityTooLarge` from the platform rather than from anything in this repo. Signing
a URL and letting the browser PUT to S3 sidesteps every one of those: the bytes never
enter this process, so the function's memory, timeout and payload limits stop being a
property of how large a record is.

`BUILD-SPEC §2b` rule 2 states the same rule for RemixKit — nothing large ever transits
the app server.

## Why boto3 is not in `requirements.txt`

Because it is already in the Lambda. AWS ships the SDK inside the Python managed
runtimes, so importing it in the function costs nothing, while vendoring it into the
bundle costs ~90 MB unpacked — mostly botocore's endpoint and service JSON — in a
deployment whose whole design is a small zip (see `requirements.txt`'s own header, and
`embed.py` reaching the network with `urllib` rather than adding httpx).

Two consequences, both deliberate:

  * The version is whatever the runtime provides, and is therefore not pinned the way
    everything else here is. That is an accepted risk for one narrow API — presigned URL
    generation and `head_object` — which has been stable across every boto3 release.
  * A **local** process needs it installed, so it is in `requirements-dev.txt` and in
    `requirements-worker.txt`. The import below is lazy and raises with that instruction
    rather than an ImportError, because the console imports this module on every request
    and must not require boto3 to render a page that has nothing to do with storage.

## No local adapter

`app/` has `storage_local.py` and it earns its place there: RemixKit is developed against
a filesystem and its `serves_public_urls = False` flag exists precisely because a
relative URL silently breaks when handed to a remote provider. Nothing here needs that.
A local adapter would exist only to let masters upload "work" on a laptop, and the way it
would work is by putting the file somewhere the classifier, the worker and the console in
Lambda cannot read — a success that is really a failure, discovered later and elsewhere.
So: configure a bucket, or the feature says it is unconfigured. `NO FALLBACKS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: How long a signed upload URL is good for. Ten minutes is chosen against the size:
#: 100 MB on a slow domestic uplink (~2 Mbit up) is roughly seven minutes, so this
#: clears a realistic worst case without leaving a write capability lying around for an
#: hour. A signature that expires mid-PUT fails the whole transfer, not the remainder.
PUT_TTL = 600

#: Downloads are read-only and re-signable at will, so this is shorter: long enough for
#: a worker to fetch a master and for a browser to start playing one.
GET_TTL = 900

#: `Content-Type` → filename suffix. Explicit, and with **no** guess for anything absent:
#: an unrecognised type yields no suffix rather than `.bin` or a mimetypes lookup that
#: silently invents `.wave`. The key is opaque to the platform — the suffix exists only
#: so a human reading a bucket listing can tell a WAV from an MP3.
_SUFFIX = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
}

#: What a master may be. Narrower than "any audio": a delivered master is lossless or it
#: is a compressed render of one, and accepting `audio/webm` from a browser recorder
#: would put something unservicable in the masters bucket under a name that claims
#: otherwise. `assets.py` enforces this; the map above is only about naming.
ACCEPTED_MIME = frozenset(_SUFFIX)


class StorageUnconfigured(RuntimeError):
    """No bucket is configured, so there is nowhere to put a master.

    Raised rather than returning a null adapter. A console that renders an upload form
    against a storage backend that discards its input is worse than one that says the
    feature is off.
    """


class ObjectMissing(RuntimeError):
    """The row says `stored` and the object is not there.

    This is the failure the migration's header declines to model as a `missing` state:
    there is no sweep to write one, so the discrepancy surfaces at the point of use with
    a message naming the key, instead of sitting in a column nobody updates.
    """


@dataclass(frozen=True)
class Head:
    """What the bucket says about an object, as opposed to what our row claims."""
    bytes: int
    mime: str
    etag: str


@runtime_checkable
class Storage(Protocol):
    """The port. Four methods, and none of them moves bytes through this process."""

    name: str
    bucket: str

    def presign_put(self, key: str, *, content_type: str,
                    expires_in: int = PUT_TTL) -> str: ...

    def presign_get(self, key: str, *, expires_in: int = GET_TTL) -> str: ...

    def head(self, key: str) -> Head | None:
        """What is at `key`, or None if nothing is. Never raises for absence."""

    def delete(self, key: str) -> None: ...


def object_key(tenant_id: str, kind: str, content_hash: str, mime: str) -> str:
    """Where an asset's bytes go: content-addressed, under the tenant and the kind.

        masters/<tenant>/<kind>/<sha256><suffix>

    The recording is deliberately **not** in the path, and that is not an oversight — it
    mirrors `asset_one_row_per_file`, the unique index on
    `(tenant_id, kind, content_hash)`. Two consequences fall out of matching them:

      * Re-uploading an unchanged file writes the identical key, so the PUT is idempotent
        at the object level as well as at the row level. No orphan copy accumulates from
        a retried upload.
      * Moving an asset to a different recording is a row update and not an object copy,
        because the path never claimed which recording it belonged to.

    The suffix is cosmetic (see `_SUFFIX`); identity is the hash.
    """
    return f"masters/{tenant_id}/{kind}/{content_hash}{_SUFFIX.get(mime, '')}"


class S3Storage:
    """The one adapter. Presigning is done by botocore, not by hand.

    SigV4 is forty lines of HMAC and it is tempting to write here — this codebase already
    reaches the network with `urllib` rather than take a dependency. The line is drawn at
    signatures: a subtly wrong canonical request produces a URL that fails with
    `SignatureDoesNotMatch` for reasons that are invisible from the outside, and the SDK
    is already present in the runtime at no bundle cost. Correctness we cannot inspect is
    exactly what a library is for.
    """

    name = "s3"

    def __init__(self, bucket: str, region: str) -> None:
        if not bucket:
            raise StorageUnconfigured(
                "PLATFORM_MASTERS_BUCKET is not set, so there is nowhere to put a "
                "master. Apply infra/terraform/spindle (it creates the bucket) and set the "
                "variable to its name.")
        self.bucket = bucket
        self.region = region
        self._client = _client(region)

    def presign_put(self, key: str, *, content_type: str,
                    expires_in: int = PUT_TTL) -> str:
        """A URL the browser PUTs to directly.

        `ContentType` is part of what gets signed, which makes it part of the contract
        rather than a hint: the PUT **must** carry exactly this `Content-Type` header or
        S3 rejects it with `SignatureDoesNotMatch`. That is the desired strictness — it
        is what stops a signed URL for an audio object being reused to upload something
        else — but it is also the single most common way this call is misused, so the
        console's uploader sends the same string it asked to have signed.
        """
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    def presign_get(self, key: str, *, expires_in: int = GET_TTL) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def head(self, key: str) -> Head | None:
        """What the bucket holds at `key`, or None.

        This is how a `pending` row becomes `stored`: the browser says it finished, and
        this is the sentence that checks. Absence is a `None` return and not an
        exception, because "the operator picked a file and never completed the upload"
        is an ordinary outcome that the console reports rather than an error condition.

        A 403 is deliberately **not** folded into None. Without `s3:ListBucket` the API
        answers 403 rather than 404 for a missing key, so treating every client error as
        absence would turn a missing IAM grant into "the upload did not finish" — a
        misconfiguration reported as a user mistake, forever, with no way to tell them
        apart. The IAM policy in `infra/terraform/spindle` grants `s3:ListBucket` for exactly this
        reason.
        """
        from botocore.exceptions import ClientError

        try:
            got = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise
        return Head(
            bytes=int(got["ContentLength"]),
            mime=got.get("ContentType", ""),
            etag=str(got.get("ETag", "")).strip('"'),
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


#: One adapter per (bucket, region), because constructing a boto3 client is not free —
#: it parses botocore's service model for S3, which is tens of milliseconds and would
#: otherwise be paid on every request that renders a track. Keyed rather than a single
#: global so a test can point at a different bucket without poisoning the next caller.
_ADAPTERS: dict[tuple[str, str], "S3Storage"] = {}


def load(bucket: str, region: str) -> Storage:
    """The configured adapter, or `StorageUnconfigured` if there is not one.

    The raise is the feature. A `load()` that returned a no-op adapter when no bucket
    is set would give the console an upload form that accepts a file and drops it, and
    the operator would find out when the classifier had nothing to listen to — days
    later, in a different part of the system. `settings.storage_configured` is what
    callers check to decide whether to offer the form at all.
    """
    if not bucket:
        raise StorageUnconfigured(
            "PLATFORM_MASTERS_BUCKET is not set, so there is nowhere to put a master. "
            "Run `terraform apply` in infra/terraform/spindle — it creates the bucket and sets "
            "the variable on the deployed console — then export it locally too.")
    key = (bucket, region)
    if key not in _ADAPTERS:
        _ADAPTERS[key] = S3Storage(bucket, region)
    return _ADAPTERS[key]


def _client(region: str):
    """The boto3 S3 client, imported at the point of use.

    Lazy because `routes.py` imports this module to render pages that never touch
    storage, and in a cold Lambda an unnecessary import is measurable latency on the
    request that pays for it. The error message names the fix rather than surfacing a
    bare ImportError from three frames down.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - present in Lambda and in dev
        # Two very different situations, and the message must not assume the common
        # one. Locally this means the venv is missing a dev dependency. In Lambda it
        # means the managed runtime stopped shipping the SDK — which AWS has signalled
        # it may eventually do — and the fix is to vendor boto3 into the bundle, not to
        # run pip on a read-only filesystem. Telling a deployed function to `pip
        # install` would send whoever reads the log looking in the wrong place.
        import os as _os

        in_lambda = bool(_os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
        raise StorageUnconfigured(
            "boto3 is not importable, so no upload URL can be signed. "
            + ("This is the deployed function, which relies on the managed runtime "
               "providing the SDK — see requirements-dev.txt for that decision. If the "
               "runtime no longer ships it, add boto3 to requirements.txt so build.sh "
               "vendors it into the bundle."
               if in_lambda else
               "This is a local process: pip install -r requirements-dev.txt")
        ) from exc

    return boto3.client(
        "s3",
        region_name=region,
        # SigV4 explicitly. Presigned URLs signed with the older SigV2 are rejected
        # outright by buckets in regions created after 2014, and the failure is a
        # signature error that says nothing about which algorithm was expected.
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )
