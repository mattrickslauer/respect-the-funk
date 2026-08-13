"""Amazon Bedrock — Titan Text Embeddings V2, by the two routes this account actually has.

`embed.py` is the port: one interface, `Vector` out, the spend gate in front. This module
is the AWS half of one adapter, and it is a separate file for two reasons. The first is
ordinary: boto3, IAM roles, S3 URIs and job polling are AWS's vocabulary, not the
embedding port's, and mixing them into `embed.py` would make the port's contract harder to
read than the thing it is protecting. The second is that Bedrock is already priced for a
second consumer — `spend.RATES` carries `bedrock:anthropic.claude-sonnet-4-5` — and that
consumer should be able to reuse the client construction and the error translation below
without importing the word "embedding".

The import runs one way only: `embed` imports `bedrock`, never the reverse. That is why
this module raises `BedrockUnavailable` rather than `embed.EmbeddingUnavailable`, and why
`embed.BedrockEmbedder` translates one into the other at the boundary. A cycle here would
be the kind of thing that works until the first `python -c "import rtf_platform.bedrock"`.

## The measurement this module is shaped by

On this account (821135790223), measured 2026-08-13 rather than read from documentation:

  * `amazon.titan-embed-text-v2:0` is `ACTIVE`, `ON_DEMAND` is a supported inference type,
    and a call that gets through returns exactly 1024 floats. Access is granted.
  * **On-demand throughput is hard zero.** Service Quotas `L-26C560CE`, *"On-demand model
    inference requests per minute for Amazon Titan Text Embeddings V2"*, applied value
    `0.0`, and — this is the part that matters — **`Adjustable: false`**. The tokens-per-
    minute quota is `0.0` as well. There is no pending increase request because there is
    no increase to request.
  * Empirically: 6/6 invocations throttled in us-east-1, 12/12 in eu-west-1 after a single
    burst success, 1/1 in us-west-2. A lone call occasionally slips through a burst
    allowance, which is the worst possible behaviour — it makes the limit look like a
    network blip rather than a permanent capability ceiling.
  * There is no Titan inference profile to route around it. `list_inference_profiles`
    returns Cohere and TwelveLabs embedding profiles and no Amazon one.
  * **Batch inference has quota but no entitlement.** Service Quotas advertises 100,000
    records per job, 100 concurrent base-model jobs, 1 GB per input file and a floor of
    100 records. Every one of those numbers is real and none of them is reachable:
    `CreateModelInvocationJob` returns `ValidationException: Your account is not
    authorized to perform this action. Please create a support case`. Verified with a
    *real* existing IAM role and a *real* bucket, in us-east-1 and us-west-2, with the
    caller's IAM simulated as `allowed` for `bedrock:CreateModelInvocationJob` and
    `ListModelInvocationJobs` succeeding. The gate fires before AWS ever looks at the
    role, so this is not a trust-policy problem.

    **This is the trap worth naming.** A quota is a number; an entitlement is a
    permission. Service Quotas publishes those default rows for every account whether or
    not the capability is switched on, so "the quota says 100,000" reads as capability
    and is not. Anyone planning against this account from the quota table alone —
    as this module's first draft did — will plan a path that cannot run.

## Therefore: two paths, neither of which runs here yet, and why they are still right

A *bulk backfill* is asynchronous by nature. Nobody is watching while 370,000 counterparty
profiles get embedded, so an S3 round trip and a queue wait cost nothing but wall clock —
and batch is where Titan capacity is *shaped* to live, at roughly half the on-demand
price. A *live* embedding is the opposite: a single party gets discovered, embedded and
shortlisted inside one request, and it cannot wait on a job queue with no SLA. So the
interactive path stays on OpenAI's on-demand embedder and the bulk path is written for
Bedrock batch.

Today that bulk path is code with a support case in front of it. It ships anyway, and not
out of optimism: the entitlement is the *only* thing missing, it is a switch AWS throws
rather than work we can do, and the alternative — waiting to write it until the case
clears — puts an untested S3-and-polling pipeline in the way of a backfill on the day it
is finally unblocked. What ships is a path whose every failure, including the entitlement
one, says exactly which of the four possible walls it hit.

Two providers writing into one vector column is normally the most dangerous thing a system
can do — `embed.py`'s header explains why a cosine distance between two models' vectors is
a well-formed float and complete nonsense. It is safe here for exactly one reason:
`embedding_model` is the second prefix column of the `party_shortlist` vector index
(migration 009), every retrieval filters it by equality, and CockroachDB will not compare
across the prefix. The architecture is not "two providers, be careful"; it is "two
providers, and the index makes mixing them impossible".

## Why `key` and `model` are different strings on the batch adapter

`spend.RATES` needs to tell batch from on-demand, because they are billed differently.
The `embedding_model` column must *not* tell them apart, because they are the same model
producing the same vectors — a batch-produced vector and an on-demand-produced vector for
the same text are comparable, and splitting the index into two halves over a billing
distinction would halve every shortlist for no reason at all. `embed.Embedder` already
carries `key` and `model` as separate fields; this is the case that justifies them.

## Why every failure becomes one loud exception, and no retries

`invoke_model` is wrapped in a bare `except Exception` that re-raises. That is not the
swallowing this codebase forbids — nothing is caught and continued past. It is the
opposite: every way a paid call can fail is funnelled into a single exception type
carrying a message an operator can act on, so a missing credential, a wrong region and a
zero quota do not present as three different unhandled botocore classes at three different
call sites.

Throttling gets its own subclass and its own message naming the quota code, because the
generic advice for a `ThrottlingException` — back off and retry — is actively wrong here.
Retrying a permanently-zero quota is an infinite loop that looks like patience.

botocore's retry policy is pinned to a single attempt for the same reason `spend.py`
refuses to retry inside the gate: an SDK-level retry is a second call the gate never saw.
It is also, at four default attempts against a zero quota, ten seconds of a Lambda's
timeout spent proving something already known.
"""

from __future__ import annotations

import io
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from rtf_platform import settings as settings_mod

#: The Bedrock model id, exactly as `InvokeModel` and `CreateModelInvocationJob` want it.
MODEL_ID = "amazon.titan-embed-text-v2:0"

#: What goes in the `embedding_model` column, for **both** paths. See the header: batch
#: and on-demand are a billing distinction, not a model one, and the vector index prefix
#: must not be split over it.
MODEL = f"bedrock:{MODEL_ID}"

#: `spend.RATES` keys. Derived from `MODEL_ID` so a model-id change cannot leave the rate
#: card silently pointing at the old one; the literals in `spend.py` stay literals because
#: that table has to be readable without importing anything.
ON_DEMAND_KEY = MODEL
BATCH_KEY = f"{MODEL}:batch"

#: Titan V2 is Matryoshka-trained and accepts exactly these widths. Checked here rather
#: than left to AWS because a `ValidationException` says "malformed input" and this says
#: which number was wrong and what the alternatives are.
SUPPORTED_DIMENSIONS = (256, 512, 1024)

#: Quota *"Records per batch inference job for Titan Text Embeddings V2"* = 100,000
#: (adjustable), measured 2026-08-13. Written down here so a 370k backfill is chunked into
#: four jobs by construction rather than discovering the ceiling from a rejected job after
#: a 1 GB upload.
BATCH_MAX_RECORDS = 100_000

#: Quota *"Minimum number of records per batch inference job"* = 100. This is the sharp
#: edge of the batch path and the reason it cannot simply replace the on-demand one: a
#: backfill of 40 parties has **no** Bedrock route at all. Callers get told that in words
#: rather than getting a job rejection from AWS twenty seconds later.
BATCH_MIN_RECORDS = 100

#: Quota *"Batch inference input file size (in GB)"* = 1. Checked against the encoded
#: JSONL before upload: an oversized file is rejected at job creation, which is after the
#: upload has already been paid for in S3 PUT requests and time.
BATCH_MAX_INPUT_BYTES = 1_000_000_000

#: How often the job poller asks. Batch jobs queue for minutes to hours with no published
#: SLA, so a tight poll buys nothing and a `GetModelInvocationJob` loop at one hertz is a
#: throttle of its own.
POLL_SECONDS = 30

#: Terminal job states. `PartiallyCompleted` is terminal *and* an error here: it means some
#: records failed, and a backfill that silently embeds 99% of a shortlist and reports
#: success is precisely the failure this codebase exists to prevent.
_TERMINAL = frozenset({"Completed", "Failed", "Stopped", "PartiallyCompleted", "Expired"})


class BedrockUnavailable(RuntimeError):
    """Bedrock could not do the thing, and no substitute was chosen on your behalf.

    Every failure in this module lands here or in a subclass. `embed.BedrockEmbedder`
    re-raises it as `embed.EmbeddingUnavailable` so the port's contract holds; the
    distinction survives in `__cause__` for anyone reading a traceback.
    """


class BedrockThrottled(BedrockUnavailable):
    """The call was refused for throughput, which on this account means the quota is zero.

    A subclass rather than a message, because this is the one Bedrock failure a caller
    might reasonably want to branch on — not to retry it, but to say "use the batch path"
    without string-matching an exception message.
    """


class BatchNotConfigured(BedrockUnavailable):
    """The batch path needs an S3 bucket and an IAM role, and one of them is missing.

    Separate from `BedrockUnavailable` because it is a deployment gap rather than a
    runtime failure: nothing was called, nothing was charged, and the fix is Terraform.
    """


class BedrockNotEntitled(BedrockUnavailable):
    """The account is not allowed to use this capability at all, at any quota.

    The distinction this class exists to preserve: a quota is a *number* and an
    entitlement is a *permission*, and Service Quotas will happily report generous
    numbers for a capability the account cannot invoke. Batch inference on this account
    reports 100,000 records per job and refuses every `CreateModelInvocationJob` with
    "your account is not authorized — please create a support case". Those default quota
    rows are published for every account whether or not batch is enabled for it, so
    reading them as evidence of capability is exactly the mistake this class is named
    after. The fix is an AWS support case and a human at AWS, measured in days; it is not
    a config change, not a role, and not something to retry.
    """


# ------------------------------------------------------------------- clients

def _boto3() -> Any:
    """boto3, imported at the point of use, or a message saying why there is none.

    Same rule `storage.py` and `mail.py` state: boto3 is ~90 MB and the Lambda Python
    runtime already ships it, so it is deliberately absent from `requirements.txt` and
    from the console bundle. This module therefore has to be *importable* without it —
    `embed.load()` must be able to raise a clean "provider not configured" on a laptop
    that has never installed boto3, not an `ImportError` from module scope.
    """
    try:
        import boto3  # noqa: PLC0415 — see docstring
    except ImportError as exc:
        raise BedrockUnavailable(
            "boto3 is not importable, so Bedrock cannot be called. The Lambda runtime "
            "ships it; a laptop or worker needs `pip install -r requirements-dev.txt`."
        ) from exc
    return boto3


def region() -> str:
    """The one region resolution in this system, borrowed rather than re-derived.

    `settings.load()` already resolves `PLATFORM_REGION` → `AWS_REGION` → `us-east-1`, and
    a second chain here would be a place for the two to disagree — the console signing an
    S3 URL in one region while the embedder invokes a model in another, which fails as an
    `AccessDeniedException` nobody can trace back to a config split.

    A region where the model is not enabled does not degrade into anything; it surfaces as
    a translated `AccessDenied`/`ResourceNotFound` naming the region, below.
    """
    return settings_mod.load().region


def _client(service: str) -> Any:
    boto3 = _boto3()
    from botocore.config import Config  # noqa: PLC0415 — arrives with boto3 or not at all

    return boto3.client(
        service,
        region_name=region(),
        # One attempt. See the header: an SDK retry is a call the spend gate never saw,
        # and against a zero quota it is four round trips to learn a constant.
        config=Config(retries={"max_attempts": 1, "mode": "standard"},
                      connect_timeout=10, read_timeout=60),
    )


def runtime_client() -> Any:
    """`bedrock-runtime` — the data plane, where `InvokeModel` lives."""
    return _client("bedrock-runtime")


def control_client() -> Any:
    """`bedrock` — the control plane. Batch jobs are created here, not on the runtime.

    Two clients for one service is an AWS API split that catches everyone once:
    `create_model_invocation_job` on a `bedrock-runtime` client raises `AttributeError`,
    not a helpful error.
    """
    return _client("bedrock")


def s3_client() -> Any:
    return _client("s3")


# ------------------------------------------------------- error translation

def _error_code(exc: BaseException) -> str:
    """The botocore error code, by duck-typing rather than by importing `ClientError`.

    `exc.response["Error"]["Code"]` is the shape every botocore `ClientError` carries.
    Reading it structurally means this module does not need botocore imported to
    *translate* an error — which matters for the tests, which fabricate failures without
    a boto3 session, and for any environment where the SDK is present but a different
    version's exception hierarchy is not what we compiled against.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            return str(error.get("Code", ""))
    return ""


def _translate(exc: BaseException, what: str) -> BedrockUnavailable:
    """One actionable message per failure mode, in the operator's terms.

    The codes below are the four that actually happen and each has a *different* fix, which
    is the whole argument for translating rather than letting botocore's own text through:
    "An error occurred (AccessDeniedException)" does not say which of "model access not
    requested", "wrong region" or "IAM policy missing bedrock:InvokeModel" it means.
    """
    code = _error_code(exc)
    where = f"{MODEL_ID} in {region()}"

    if code in {"ThrottlingException", "TooManyRequestsException",
                "ServiceQuotaExceededException"}:
        return BedrockThrottled(
            f"{what}: {where} was throttled. On this account the on-demand quota for "
            f"this model is **zero** — Service Quotas L-26C560CE, applied value 0.0, "
            f"Adjustable: false, measured 2026-08-13 — so this is a permanent capability "
            f"limit and not a transient blip. Do not retry. Use the batch path "
            f"(`bedrock.embed_batch_job`, rate key {BATCH_KEY!r}), or embed via OpenAI "
            f"with RTF_EMBED_PROVIDER=openai."
        )
    if code in {"AccessDeniedException", "UnrecognizedClientException"}:
        return BedrockUnavailable(
            f"{what}: access denied for {where}. Either model access has not been granted "
            f"in this region, or the caller's IAM policy is missing bedrock:InvokeModel / "
            f"bedrock:CreateModelInvocationJob. Set PLATFORM_REGION to the region where "
            f"access was granted rather than assuming the default."
        )
    # Account-level entitlement gating arrives as a `ValidationException` — the same code
    # AWS uses for a malformed body — so the *message* is the only thing that separates
    # "you typed the model id wrong" from "your account may not do this at all". Matching
    # on prose is a last resort and it is used here because the alternative is telling an
    # operator to check their request body when no request body would ever have worked.
    # Verified 2026-08-13 against a real, existing role and a real bucket, in two regions.
    detail = str(exc).lower()
    if "not authorized" in detail and "support case" in detail:
        return BedrockNotEntitled(
            f"{what}: account 821135790223 is not entitled to this Bedrock capability in "
            f"{region()}. AWS returns this before it ever validates the role or the "
            f"bucket, so it is not a permissions or configuration problem — batch "
            f"inference is gated per account and released by AWS support. Note that "
            f"Service Quotas still advertises {BATCH_MAX_RECORDS} records per job; those "
            f"are default rows published for every account and are NOT evidence that the "
            f"capability is enabled. Open a support case; there is nothing to retry and "
            f"no code change that helps. Underlying: {exc}"
        )
    if code in {"ResourceNotFoundException", "ValidationException"}:
        return BedrockUnavailable(
            f"{what}: Bedrock rejected the request for {where} ({code}). The model id or "
            f"the request body is wrong for this region — a model available in us-east-1 "
            f"is not automatically available elsewhere. Underlying: {exc}"
        )
    if code:
        return BedrockUnavailable(f"{what}: {where} failed with {code}: {exc}")
    return BedrockUnavailable(f"{what}: {where} failed: {type(exc).__name__}: {exc}")


# --------------------------------------------------------- the request body

def request_body(text: str, *, dimensions: int, normalize: bool = True) -> dict[str, Any]:
    """The Titan V2 payload, with every field stated rather than defaulted.

    `dimensions` has no default **on purpose**. The schema width lives in `embed.py` as
    `DIMENSIONS` and a second copy of `1024` here is a second thing to forget to change;
    worse, a default would let a caller who forgot to pass it get 1024-wide vectors by
    luck rather than by intent, which is indistinguishable from correct right up until
    somebody changes the column.

    `normalize` is sent explicitly even though Titan's own default is `true`. Two reasons.
    An API default is a thing AWS can change under a running backfill, and half an index
    of unit vectors and half not is silent corruption — cosine is scale-invariant so
    nothing would even look wrong, until somebody adds an inner-product query or compares
    raw distances across providers. And OpenAI returns unit-norm vectors, so pinning this
    to `true` is what makes the two providers' outputs the same *kind* of object even
    though the index will never compare them.

    Empty text is refused here rather than sent. Titan rejects it with a
    `ValidationException`, but the more useful statement is the local one: a party whose
    profile text is empty is an upstream bug, and embedding whitespace would write a
    meaningless vector into a shortlist index where it would sit forever, retrievable and
    wrong.
    """
    if dimensions not in SUPPORTED_DIMENSIONS:
        raise BedrockUnavailable(
            f"{MODEL_ID} supports {SUPPORTED_DIMENSIONS} dimensions, not {dimensions}."
        )
    if not text or not text.strip():
        raise BedrockUnavailable(
            "Refusing to embed empty text. An empty profile is an upstream bug, and the "
            "vector it would produce is indistinguishable from a real one once written."
        )
    return {"inputText": text, "dimensions": dimensions, "normalize": normalize}


@dataclass(frozen=True)
class TitanEmbedding:
    """One embedding, plus what Titan says it actually charged for.

    `input_tokens` is the model's own count, which is the only true number in the cost
    path — `embed.estimate_tokens` deliberately over-estimates because the gate has to
    decide *before* the call. Carried out of this module rather than discarded so a
    backfill can report how far the estimator drifted, without the gate ever depending on
    a figure that only exists after the money is spent.
    """

    values: list[float]
    input_tokens: int


def parse_output(payload: Any, *, dimensions: int, where: str) -> TitanEmbedding:
    """Titan's response, checked hard, because every way this can be wrong is silent.

    A short vector fails at the database, far from here. A vector of the right length
    containing a `NaN` fails *nowhere*: every cosine distance against it is `NaN`, the
    shortlist quietly stops ranking, and nothing raises. A missing `inputTextTokenCount`
    would turn a cost figure into zero, which is the one direction a spend system must
    never round.
    """
    if not isinstance(payload, dict):
        raise BedrockUnavailable(f"{where}: expected a JSON object, got {type(payload).__name__}.")

    values = payload.get("embedding")
    if not isinstance(values, list):
        keys = sorted(payload) if payload else "nothing"
        raise BedrockUnavailable(f"{where}: response has no 'embedding' list; it has {keys}.")
    if len(values) != dimensions:
        raise BedrockUnavailable(
            f"{where}: asked for {dimensions} dimensions and got {len(values)}."
        )

    floats: list[float] = []
    for index, value in enumerate(values):
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not math.isfinite(value):
            raise BedrockUnavailable(
                f"{where}: dimension {index} is {value!r}, which is not a finite number. "
                f"A NaN in a vector makes every distance against it NaN, and nothing "
                f"downstream raises — the shortlist just stops ranking."
            )
        floats.append(float(value))

    tokens = payload.get("inputTextTokenCount")
    if not isinstance(tokens, int) or isinstance(tokens, bool):
        raise BedrockUnavailable(
            f"{where}: response has no integer 'inputTextTokenCount' (got {tokens!r}), so "
            f"the call cannot be costed. An uncosted paid call is not allowed to succeed."
        )
    return TitanEmbedding(values=floats, input_tokens=tokens)


# ----------------------------------------------------------- on-demand path

def embed_one(client: Any, text: str, *, dimensions: int) -> TitanEmbedding:
    """One `InvokeModel`. One text. That is Titan's API, not an oversight.

    Titan Text Embeddings V2 takes a single `inputText` per invocation — there is no
    array form — so a batch on this path is a loop, and a loop is exactly what the zero
    RPM quota kills. Kept live and tested anyway: it is correct, it is what a quota change
    would flip to, and it is the thing the batch path is measured against.
    """
    body = request_body(text, dimensions=dimensions)
    try:
        response = client.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(body),
            accept="application/json",
            contentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001 — funnelled, never swallowed; see the header
        raise _translate(exc, "InvokeModel") from exc

    stream = response.get("body") if isinstance(response, dict) else None
    if stream is None or not hasattr(stream, "read"):
        raise BedrockUnavailable(
            f"InvokeModel returned no readable body for {MODEL_ID}; got {response!r}."
        )
    try:
        payload = json.loads(stream.read())
    except ValueError as exc:
        raise BedrockUnavailable(f"InvokeModel returned non-JSON for {MODEL_ID}: {exc}") from exc

    return parse_output(payload, dimensions=dimensions, where="InvokeModel")


# --------------------------------------------------------------- batch path

@dataclass(frozen=True)
class BatchConfig:
    """Where batch input and output live, and who Bedrock becomes to reach them.

    Bedrock does not read S3 as you. It assumes `role_arn` — a role whose trust policy
    names `bedrock.amazonaws.com` — and reads and writes with *that* identity. Which means
    the batch path has a deployment prerequisite the on-demand path does not: a role that,
    on this account as of 2026-08-13, **does not exist** (48 roles, none trusting Bedrock).
    Hence `BatchNotConfigured` rather than a stub that appears to work.
    """

    bucket: str
    prefix: str
    role_arn: str

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"


def load_batch_config() -> BatchConfig:
    """Read the batch prerequisites, or say precisely which one is missing.

    Bucket and role are required with no default of any kind: a default bucket would be
    somebody else's data and a default role would be a permissions error dressed up as a
    typo. The prefix is defaulted because a key prefix is a naming convention, not a
    capability — getting it wrong writes to the wrong folder of a bucket you named on
    purpose, which is recoverable, unlike the other two.
    """
    bucket = os.environ.get("RTF_BEDROCK_BATCH_BUCKET", "").strip()
    role_arn = os.environ.get("RTF_BEDROCK_BATCH_ROLE_ARN", "").strip()
    missing = [name for name, value in
               (("RTF_BEDROCK_BATCH_BUCKET", bucket), ("RTF_BEDROCK_BATCH_ROLE_ARN", role_arn))
               if not value]
    if missing:
        raise BatchNotConfigured(
            f"Bedrock batch inference needs {' and '.join(missing)}. The role must trust "
            f"bedrock.amazonaws.com and hold s3:GetObject/s3:PutObject/s3:ListBucket on "
            f"the bucket; Bedrock reads and writes as that role, not as the caller. No "
            f"such role exists on this account yet — this is a Terraform change, not a "
            f"missing export."
        )
    prefix = os.environ.get("RTF_BEDROCK_BATCH_PREFIX", "bedrock-embed").strip().strip("/")
    return BatchConfig(bucket=bucket, prefix=prefix, role_arn=role_arn)


def record_id(index: int) -> str:
    """An 11-character alphanumeric id, which is what Bedrock's JSONL schema demands.

    Derived from the position rather than random, so the id *encodes* the input order.
    Batch output is not returned in input order — it is not returned in any documented
    order — so the mapping back is by `recordId`, and making that id self-describing means
    a truncated or partially-recovered output file can still be placed correctly instead
    of being a bag of vectors with no idea which party they belong to.
    """
    if not 0 <= index <= 9_999_999_999:
        raise BedrockUnavailable(f"record index {index} does not fit an 11-character id.")
    return f"r{index:010d}"


def input_jsonl(texts: Sequence[str], *, dimensions: int) -> bytes:
    """The whole input file, encoded, with the record count and size checked first.

    Both quota limits are enforced here rather than at job creation, because by the time
    AWS rejects the job the 1 GB upload has already happened. The floor is the one that
    surprises people: fewer than 100 records is not a small job, it is *no job*, and the
    caller needs to hear that in a sentence that names the alternative.
    """
    if len(texts) < BATCH_MIN_RECORDS:
        raise BedrockUnavailable(
            f"Bedrock batch inference requires at least {BATCH_MIN_RECORDS} records and "
            f"this job has {len(texts)}. There is no Bedrock route for a backfill this "
            f"small on this account — the on-demand quota is zero — so embed these via "
            f"OpenAI, or wait until enough rows have accumulated."
        )
    if len(texts) > BATCH_MAX_RECORDS:
        raise BedrockUnavailable(
            f"Bedrock batch inference allows at most {BATCH_MAX_RECORDS} records per job "
            f"and this one has {len(texts)}. Chunk the backfill — `embed.embed_bulk` does."
        )

    buffer = io.StringIO()
    for index, text in enumerate(texts):
        buffer.write(json.dumps({
            "recordId": record_id(index),
            "modelInput": request_body(text, dimensions=dimensions),
        }))
        buffer.write("\n")
    encoded = buffer.getvalue().encode("utf-8")

    if len(encoded) > BATCH_MAX_INPUT_BYTES:
        raise BedrockUnavailable(
            f"Batch input is {len(encoded)} bytes, over the {BATCH_MAX_INPUT_BYTES}-byte "
            f"per-file quota. Chunk by size as well as by record count."
        )
    return encoded


def parse_batch_output(body: str, count: int, *, dimensions: int) -> list[TitanEmbedding]:
    """Output JSONL back into input order, refusing anything less than all of it.

    Three separate silent failures live in this function and each gets an explicit check:

      * **Order.** Bedrock does not promise output order. Reading the file top to bottom
        and zipping it against the input is the same mistake `OpenAIEmbedder` guards
        against by sorting on `index` — misassigned vectors do not raise, they just make
        every shortlist wrong in a way that reads as "the model is not very good".
      * **Per-record errors.** A record can fail on its own, and the job still reports
        `Completed`. That record has an `error` field and no `modelOutput`.
      * **Missing records.** A short output file is the batch equivalent of a truncated
        response. Embedding 99,997 of 100,000 parties and reporting success is the
        `PartiallyCompleted` failure this module refuses to launder.
    """
    found: dict[str, TitanEmbedding] = {}
    for line_number, line in enumerate(body.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise BedrockUnavailable(
                f"batch output line {line_number} is not JSON: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise BedrockUnavailable(f"batch output line {line_number} is not an object.")

        rid = record.get("recordId")
        if not isinstance(rid, str) or not rid:
            raise BedrockUnavailable(
                f"batch output line {line_number} has no recordId, so its vector cannot "
                f"be attributed to an input. It is not safe to guess by position."
            )
        if record.get("error"):
            raise BedrockUnavailable(
                f"batch record {rid} failed: {record['error']!r}. The job may still say "
                f"Completed; a per-record failure does not fail the job."
            )
        found[rid] = parse_output(record.get("modelOutput"), dimensions=dimensions,
                                  where=f"batch record {rid}")

    expected = [record_id(i) for i in range(count)]
    missing = [rid for rid in expected if rid not in found]
    if missing:
        raise BedrockUnavailable(
            f"batch output is missing {len(missing)} of {count} records "
            f"(first: {missing[0]}). A partial backfill reported as complete is worse "
            f"than a failed one, because nothing downstream will ever look again."
        )
    return [found[rid] for rid in expected]


@dataclass(frozen=True)
class BatchJob:
    """A submitted job, and where its output will land."""

    arn: str
    input_key: str
    output_prefix: str

    @property
    def job_id(self) -> str:
        return self.arn.rsplit("/", 1)[-1]


def submit_batch(texts: Sequence[str], *, dimensions: int,
                 config: BatchConfig | None = None,
                 control: Any = None, s3: Any = None,
                 job_name: str | None = None) -> BatchJob:
    """Upload the input file and create the job. Does not wait.

    Split from the waiting half deliberately. A job runs for an unbounded time with no
    published SLA, and a function that both submits and blocks gives a caller that dies
    mid-wait no way to find the job again — the ARN would only ever have existed on a
    stack frame. Returning `BatchJob` means a crashed backfill is resumable.
    """
    config = config or load_batch_config()
    control = control if control is not None else control_client()
    s3 = s3 if s3 is not None else s3_client()

    payload = input_jsonl(texts, dimensions=dimensions)
    # Names are unique-by-clock rather than by content hash: two identical backfills are a
    # legitimate thing to run (a re-embed after a text change produces the same job name
    # from the same texts), and a name collision is a job rejection, not a cache hit.
    stamp = job_name or f"rtf-embed-{int(time.time())}"
    input_key = f"{config.prefix}/{stamp}/input.jsonl"
    output_prefix = f"{config.prefix}/{stamp}/out"

    try:
        s3.put_object(Bucket=config.bucket, Key=input_key, Body=payload,
                      ContentType="application/jsonl")
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, f"PutObject s3://{config.bucket}/{input_key}") from exc

    try:
        response = control.create_model_invocation_job(
            jobName=stamp,
            roleArn=config.role_arn,
            modelId=MODEL_ID,
            inputDataConfig={"s3InputDataConfig": {
                "s3Uri": config.uri(input_key), "s3InputFormat": "JSONL"}},
            outputDataConfig={"s3OutputDataConfig": {
                "s3Uri": config.uri(output_prefix) + "/"}},
        )
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "CreateModelInvocationJob") from exc

    arn = response.get("jobArn") if isinstance(response, dict) else None
    if not isinstance(arn, str) or not arn:
        raise BedrockUnavailable(
            f"CreateModelInvocationJob returned no jobArn ({response!r}), so the job it "
            f"may have started is now unreachable and unstoppable."
        )
    return BatchJob(arn=arn, input_key=input_key, output_prefix=output_prefix)


def await_batch(job: BatchJob, *, control: Any = None, timeout_seconds: int = 6 * 3600,
                poll_seconds: int = POLL_SECONDS, sleep: Any = time.sleep) -> str:
    """Poll until the job reaches a terminal state, then insist that state is `Completed`.

    A timeout raises with the ARN in the message rather than returning something falsy:
    the job is still running on AWS's side after we stop watching, it will still bill, and
    a caller that got `None` back would have no handle on it. `PartiallyCompleted` is
    treated as failure for the reason `parse_batch_output` gives — an incomplete backfill
    that reports success is never revisited.
    """
    control = control if control is not None else control_client()
    deadline = time.monotonic() + timeout_seconds
    status = "Unknown"
    message = ""
    while True:
        try:
            described = control.get_model_invocation_job(jobIdentifier=job.arn)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, f"GetModelInvocationJob {job.arn}") from exc
        status = str(described.get("status", "")) or "Unknown"
        message = str(described.get("message", ""))
        if status in _TERMINAL:
            break
        if time.monotonic() >= deadline:
            raise BedrockUnavailable(
                f"batch job {job.arn} was still {status} after {timeout_seconds}s. It is "
                f"still running on AWS and will still bill; stop it with "
                f"StopModelInvocationJob or resume waiting with the same ARN."
            )
        sleep(poll_seconds)

    if status != "Completed":
        raise BedrockUnavailable(
            f"batch job {job.arn} finished as {status}: {message or 'no detail given'}. "
            f"Nothing is written for a job that did not complete — a partial embedding "
            f"set is not a smaller success, it is an index with holes in it."
        )
    return status


def fetch_batch_output(job: BatchJob, count: int, *, dimensions: int,
                       config: BatchConfig | None = None, s3: Any = None
                       ) -> list[TitanEmbedding]:
    """Find the `.out` file under the job's output prefix and decode it.

    Bedrock writes output to `<s3Uri>/<jobId>/<input file name>.out`, alongside a
    `manifest.json.out` that summarises the run. Both are discovered by listing rather
    than reconstructed by string-building, because the exact layout is AWS's to change and
    a hardcoded key that stops matching would present as "the job produced no output".
    """
    config = config or load_batch_config()
    s3 = s3 if s3 is not None else s3_client()
    prefix = f"{job.output_prefix}/{job.job_id}/"

    try:
        listing = s3.list_objects_v2(Bucket=config.bucket, Prefix=prefix)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, f"ListObjectsV2 s3://{config.bucket}/{prefix}") from exc

    keys = [item["Key"] for item in listing.get("Contents", [])
            if item["Key"].endswith(".out") and "manifest" not in item["Key"]]
    if len(keys) != 1:
        raise BedrockUnavailable(
            f"expected exactly one output file under s3://{config.bucket}/{prefix}, "
            f"found {len(keys)}: {keys}. Guessing which one holds the vectors is not a "
            f"decision this can make for you."
        )
    try:
        body = s3.get_object(Bucket=config.bucket, Key=keys[0])["Body"].read()
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, f"GetObject s3://{config.bucket}/{keys[0]}") from exc

    return parse_batch_output(body.decode("utf-8"), count, dimensions=dimensions)


def embed_batch_job(texts: Sequence[str], *, dimensions: int,
                    config: BatchConfig | None = None,
                    control: Any = None, s3: Any = None,
                    timeout_seconds: int = 6 * 3600,
                    poll_seconds: int = POLL_SECONDS,
                    sleep: Any = time.sleep) -> list[TitanEmbedding]:
    """Submit, wait, read back — one job's worth of texts, in input order.

    The whole batch path in one call, for the caller who *can* block. `submit_batch` and
    `await_batch` stay public underneath it because a six-hour wait is not something every
    caller can hold, and a backfill that wants to submit four jobs and poll them together
    needs the pieces rather than the composition.
    """
    config = config or load_batch_config()
    job = submit_batch(texts, dimensions=dimensions, config=config, control=control, s3=s3)
    await_batch(job, control=control, timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds, sleep=sleep)
    return fetch_batch_output(job, len(texts), dimensions=dimensions, config=config, s3=s3)


def total_tokens(embeddings: Iterable[TitanEmbedding]) -> int:
    """What Titan actually counted, for reconciling against `embed.estimate_tokens`.

    The gate never uses this — it cannot, it has to decide before the call. It exists so a
    backfill can report "estimated 4.1M tokens, charged for 3.6M" instead of leaving the
    estimator's accuracy as folklore.
    """
    return sum(e.input_tokens for e in embeddings)
