"""The embedding port. One interface, three adapters, and the spend gate in front of all.

`PLATFORM-SPEC §8` assumes Bedrock is the embedding provider. For interactive work on this
account it cannot be, and — measured 2026-08-13 — it never will be: Titan Text Embeddings
V2 is `ACTIVE` and access is granted, but the on-demand quota is **0 requests per minute**
and Service Quotas reports that quota as **not adjustable**. There is no increase to wait
on. `bedrock.py`'s header carries the full measurement.

Bedrock batch inference is the natural home for a backfill and is *also* blocked here, by
a different wall: the account is not entitled to `CreateModelInvocationJob` at all, which
AWS releases through a support case. Service Quotas advertises 100,000 records a job
regardless, which is why that looked like a route. It is not one yet.

So there are three adapters behind one port, and today exactly one of them can run:

  * `OpenAIEmbedder` — on-demand, interactive. The only working path, live and bulk.
  * `BedrockEmbedder` — on-demand Titan. Correct, tested, and blocked by a quota that
    cannot be raised. Kept because "unusable today" and "wrong" are different states.
  * `BedrockBatchEmbedder` — asynchronous Titan via S3, driven by `embed_bulk`. Correct,
    tested, and blocked by an account entitlement. The right shape for a backfill the day
    AWS switches it on, and until then a path that says precisely why it cannot run.

That split is not a hedge, and it is not aspiration either. A live embedding happens inside
one request and cannot wait on a job queue with no SLA; a 370,000-row backfill has nobody
watching and can. The adapters differ because the *latency requirements* differ — which is
the honest reason to have two, and stays true whichever of them the account can reach.

The one thing a vector store must never do is mix embeddings from two models in one index,
and `embedding_model` being a prefix column of `party_shortlist` is what makes coexistence
mechanical rather than careful. See below.

## The trap this module exists to prevent

Embeddings from different models are **not comparable**. A cosine distance between an
OpenAI vector and a Titan vector is a well-formed float and complete nonsense. Nothing
raises, nothing logs, and the shortlist quietly becomes random — the single worst failure
mode available to us, because it looks like it works.

Two mechanisms, because one is a convention and conventions rot:

  * every embedding written carries the `model` that produced it, on the row, and
  * every retrieval filters to one model as an equality predicate, which is also what
    `PLATFORM-SPEC §6`'s amendment needs for the vector index prefix to be used at all.

Migration `007` adds the `model` column to `party_chunk`, which had an `embedding` and no
way to say where it came from. Migration `009` makes `embedding_model` the second prefix
column of the `party_shortlist` vector index, which upgrades the convention into a
guarantee: CockroachDB will not walk across a prefix it is filtering by equality, so an
OpenAI vector is not merely *unlikely* to be compared against a Titan one, it is
unreachable from a query that asked for the other model.

Note what that does **not** mean. `key` and `model` are separate fields on `Embedder`
because Bedrock batch and Bedrock on-demand are billed differently and must be *priced*
apart, while producing identical vectors that must not be *indexed* apart. Splitting the
index over a billing distinction would halve every shortlist for no reason.

## Why 1024 dimensions

Fixed by the schema — `party_fact.embedding` and `party_chunk.embedding` are
`VECTOR(1024)`, chosen when Titan (natively 1024) was the assumed provider. OpenAI's
`text-embedding-3` family is Matryoshka-trained and supports truncation to an arbitrary
width via the `dimensions` parameter, so it can meet the schema rather than the schema
meeting it. Both providers therefore land on the same width and swapping is a config
change instead of a migration.

Truncation is not free — a 1024-wide `text-embedding-3-small` vector retrieves slightly
worse than its native 1536. Accepted: matching the column and keeping one width across
providers is worth more than the margin.

## No HTTP dependency

`urllib.request` rather than httpx or requests. Every dependency here is bundled into the
Lambda zip and the zip is the cold-start cost, which is the same argument
`requirements.txt` already makes. The provider APIs are one POST each.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Callable, Protocol, Sequence

from spindle import bedrock, spend

#: Fixed by `VECTOR(1024)` in migrations 004 and 005. Changing this is a migration, not
#: a setting, so it is a constant rather than an environment variable.
DIMENSIONS = 1024

#: How many texts go up in one request. Small on purpose: `PLATFORM-SPEC §10` risk 4
#: records that CockroachDB's vector index degrades on large batch inserts and that
#: `IMPORT INTO` is unsupported on a table carrying one. The write side has to trickle,
#: so there is no point batching the read side any harder than this.
BATCH = 16


class EmbeddingUnavailable(RuntimeError):
    """No provider is configured, or the configured one cannot be reached.

    Distinct from `spend.SpendRefused`, which means a provider *is* available and we
    chose not to pay. Collapsing the two would hide a missing API key behind a message
    about ceilings.

    `completed` exists because Titan on-demand embeds **one text per invocation**, so a
    batch of sixteen is sixteen billable calls and a failure on the ninth means eight were
    paid for. `spend.Gate.record` is deliberately called after a successful call — see its
    docstring — which is right for a provider whose batch is one HTTP request and wrong
    for one whose batch is a loop. Carrying the count lets `embed_batch` record what was
    actually incurred instead of recording zero, which is the direction a spend system is
    never allowed to be wrong in.
    """

    def __init__(self, message: str, *, completed: int = 0) -> None:
        super().__init__(message)
        self.completed = completed


@dataclass(frozen=True)
class Vector:
    """One embedding, and the model that produced it.

    The model travels with the values rather than being looked up later, because the
    caller writing the row is the last place that still knows for certain.
    """

    values: list[float]
    model: str

    def __post_init__(self) -> None:
        if len(self.values) != DIMENSIONS:
            raise EmbeddingUnavailable(
                f"{self.model} returned {len(self.values)} dimensions, "
                f"but the schema column is VECTOR({DIMENSIONS})."
            )

    def literal(self) -> str:
        """The `VECTOR` literal CockroachDB accepts: `[0.1,0.2,…]`.

        Rendered here rather than at each call site so no one is tempted to pass a
        Python list and let the driver guess.
        """
        return "[" + ",".join(repr(float(v)) for v in self.values) + "]"


Post = Callable[[str, dict, dict], dict]
"""`(url, headers, body) -> decoded JSON`. Injected so tests never touch a network."""


def _post(url: str, headers: dict, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:  # noqa: PERF203 — the body is the diagnosis
        detail = exc.read().decode(errors="replace")[:500]
        raise EmbeddingUnavailable(f"{url} returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise EmbeddingUnavailable(f"{url} unreachable: {exc.reason}") from exc


class Embedder(Protocol):
    """What every provider adapter has to offer."""

    key: str
    """The `spend.RATES` key. A provider absent from that table cannot be called."""

    model: str
    """Recorded on every row written, and filtered on at every retrieval."""

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


@dataclass
class OpenAIEmbedder:
    """`text-embedding-3-small`, truncated to 1024 dimensions."""

    api_key: str
    post: Post = _post
    key: str = "openai:text-embedding-3-small"
    model: str = "openai:text-embedding-3-small"

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        payload = self.post(
            "https://api.openai.com/v1/embeddings",
            {"authorization": f"Bearer {self.api_key}"},
            {"model": "text-embedding-3-small", "input": list(texts),
             "dimensions": DIMENSIONS},
        )
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingUnavailable(
                f"expected {len(texts)} embeddings, got "
                f"{len(data) if isinstance(data, list) else type(data).__name__}"
            )
        # Sort by index rather than trusting order. The API documents that it returns
        # them in order; relying on that silently misassigns every vector if it ever
        # stops being true, and misassigned vectors do not raise.
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [Vector(list(d["embedding"]), self.model) for d in ordered]


@dataclass
class BedrockEmbedder:
    """Titan Text Embeddings V2 on demand, natively 1024.

    Unusable on this account and — unlike the earlier reading of this situation — not
    pending anything: the RPM quota is zero and Service Quotas marks it `Adjustable:
    false`. Kept live and tested anyway, because "cannot run here today" and "wrong" are
    different states, and the day this account is replaced or the model gains an inference
    profile the swap should be a variable rather than a rewrite under deadline.

    All the AWS mechanics live in `bedrock.py`; this is the ten lines that make them fit
    the port. The exception translation is the interesting line: `bedrock` raises its own
    type so it can be reused by a non-embedding caller, and the port promises
    `EmbeddingUnavailable`, so the boundary converts. `__cause__` keeps the specific
    diagnosis — including `BedrockThrottled`, which names the zero quota — visible in the
    traceback rather than flattening it into a generic message.
    """

    client: object  # boto3 bedrock-runtime; typed loosely to keep boto3 out of the zip
    key: str = bedrock.ON_DEMAND_KEY
    model: str = bedrock.MODEL

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            try:
                embedding = bedrock.embed_one(self.client, text, dimensions=DIMENSIONS)
            except bedrock.BedrockUnavailable as exc:
                # `len(out)` is exactly how many invocations already billed. See
                # `EmbeddingUnavailable.completed`.
                raise EmbeddingUnavailable(str(exc), completed=len(out)) from exc
            out.append(Vector(embedding.values, self.model))
        return out


@dataclass
class BedrockBatchEmbedder:
    """Titan Text Embeddings V2 through Bedrock batch inference — written, not yet enabled.

    `embed()` here is not a request — it uploads a JSONL file to S3, creates a model
    invocation job, polls it to completion and reads the vectors back out. That can take
    hours, which is why this adapter must never be reached through `embed_batch`
    (sixteen-record jobs, below Bedrock's hundred-record floor, would be rejected anyway)
    and has `embed_bulk` instead.

    `key` and `model` differ, and that is the point of them being separate fields: batch
    is billed at roughly half on-demand so it needs its own rate-card entry, while the
    vectors are the same model's output and must carry the same `embedding_model` so the
    `party_shortlist` index does not split in two over a price.
    """

    config: object | None = None      # bedrock.BatchConfig; None means read the environment
    control: object | None = None     # boto3 'bedrock' control-plane client
    s3: object | None = None
    timeout_seconds: int = 6 * 3600
    key: str = bedrock.BATCH_KEY
    model: str = bedrock.MODEL

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        try:
            embeddings = bedrock.embed_batch_job(
                list(texts), dimensions=DIMENSIONS,
                config=self.config, control=self.control, s3=self.s3,  # type: ignore[arg-type]
                timeout_seconds=self.timeout_seconds,
            )
        except bedrock.BedrockUnavailable as exc:
            # `completed=0` and not `len(texts)`: a batch job that did not finish produces
            # no usable output, but it may well have billed for the records it processed.
            # We cannot know how many from here, and inventing a number to record would be
            # worse than recording none — `await_batch` puts the job ARN in the message so
            # the true figure is recoverable from AWS.
            raise EmbeddingUnavailable(str(exc), completed=0) from exc
        return [Vector(e.values, self.model) for e in embeddings]


def estimate_tokens(texts: Sequence[str]) -> int:
    """Rough token count for the spend gate, rounded up.

    Four characters per token is the usual English approximation. The gate needs a
    number *before* the call, and the only safe direction to be wrong in is expensive,
    so this rounds up and adds a per-text allowance rather than truncating.
    """
    return sum(len(t) // 4 + 2 for t in texts)


def load(post: Post = _post) -> Embedder:
    """Build the configured provider, or say clearly that there is none.

    `RTF_EMBED_PROVIDER` selects; absent, it infers from which key is present, so a
    developer with `OPENAI_API_KEY` already exported does not also have to learn a
    second variable. Never guesses when both are possible — an ambiguous configuration
    that silently picks one is how an index ends up with two models in it.

    `bedrock` and `bedrock-batch` are two selections rather than one with a flag, because
    they behave nothing alike: one returns in milliseconds and one blocks for hours, and a
    caller that got the wrong one would not find out until production. Neither is ever
    inferred — Bedrock is never what you get by default, because on this account
    on-demand cannot work and batch needs infrastructure that must be deployed on purpose.
    """
    choice = os.environ.get("RTF_EMBED_PROVIDER", "").strip().lower()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if choice == "openai" or (not choice and openai_key):
        if not openai_key:
            raise EmbeddingUnavailable("RTF_EMBED_PROVIDER=openai but OPENAI_API_KEY is unset.")
        return OpenAIEmbedder(api_key=openai_key, post=post)

    if choice == "bedrock":
        try:
            return BedrockEmbedder(client=bedrock.runtime_client())
        except bedrock.BedrockUnavailable as exc:
            raise EmbeddingUnavailable(str(exc)) from exc

    if choice == "bedrock-batch":
        try:
            # Read the configuration now rather than at first use. A backfill that
            # discovers its IAM role is missing after selecting ten thousand rows has
            # wasted the expensive half of the work; this fails at startup instead.
            return BedrockBatchEmbedder(config=bedrock.load_batch_config())
        except bedrock.BedrockUnavailable as exc:
            raise EmbeddingUnavailable(str(exc)) from exc

    raise EmbeddingUnavailable(
        "No embedding provider configured. Set OPENAI_API_KEY, or set "
        "RTF_EMBED_PROVIDER to 'openai', 'bedrock-batch' (bulk backfills; needs "
        "RTF_BEDROCK_BATCH_BUCKET and RTF_BEDROCK_BATCH_ROLE_ARN) or 'bedrock' "
        "(on-demand Titan, whose quota on this account is a non-adjustable zero)."
    )


def embed_batch(gate: spend.Gate, embedder: Embedder,
                texts: Sequence[str]) -> tuple[list[Vector], Decimal]:
    """Embed through the gate, in batches, returning the vectors and what they cost.

    The gate is checked **per batch rather than once for the whole list**, so a long
    backfill stops at the ceiling with the batches it already paid for banked, instead
    of either being refused wholesale at the start or blowing through the limit at the
    end. `spend.SpendRefused` propagates — the caller decides whether a partial backfill
    is worth resuming, because that is a judgement about the work and not about money.
    """
    if not texts:
        return [], Decimal("0")

    vectors: list[Vector] = []
    spent = Decimal("0")
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        cost = gate.check(embedder.key, tokens_in=estimate_tokens(chunk))
        try:
            vectors.extend(embedder.embed(chunk))
        except EmbeddingUnavailable as exc:
            # A provider whose batch is a loop can fail having already billed for part of
            # it. Record that part before re-raising: the alternative is a run that spent
            # money and reports zero, which the daily ceiling then hands out twice.
            # Apportioned by text count rather than by token, and rounded **up**, because
            # the gate's whole design is to be wrong in the expensive direction.
            spent += _record_partial(gate, cost, exc.completed, len(chunk))
            raise
        gate.record(cost)
        spent += cost
    return vectors, spent


def _record_partial(gate: spend.Gate, cost: Decimal, completed: int, total: int) -> Decimal:
    """Bank the fraction of a batch that was paid for before it failed."""
    if completed <= 0 or total <= 0:
        return Decimal("0")
    partial = (cost * Decimal(completed) / Decimal(total)).quantize(
        Decimal("0.000001"), rounding=ROUND_UP)
    gate.record(partial)
    return partial


def embed_bulk(gate: spend.Gate, embedder: Embedder, texts: Sequence[str],
               *, job_size: int = bedrock.BATCH_MAX_RECORDS
               ) -> tuple[list[Vector], Decimal]:
    """Embed a backfill through a provider whose unit of work is a job, not a request.

    `embed_batch`'s sixteen-text chunking exists because its providers are synchronous and
    a ceiling should stop a backfill mid-flight with what it already paid for banked. None
    of that applies here. Bedrock batch has a floor of 100 records and a ceiling of
    100,000 per job, so sixteen is not a small job, it is a rejected one; and the money is
    committed the moment the job is created, so there is no mid-flight to stop.

    The gate is therefore checked once per *job*, before it is submitted, which is the
    last moment a refusal is free. A 370,000-row backfill is four jobs and four gate
    checks, and `spend.SpendRefused` on the third leaves two jobs' worth of vectors
    already returned and paid for — the same partial-progress property `embed_batch` has,
    at the granularity this provider actually offers.

    Deliberately *not* a special case inside `embed_batch`. That function is called from
    four places that all embed a handful of rows inside a request; teaching it to
    sometimes block for six hours would be the kind of surprise that only shows up in
    production.
    """
    if not texts:
        return [], Decimal("0")
    if job_size > bedrock.BATCH_MAX_RECORDS:
        raise EmbeddingUnavailable(
            f"job_size {job_size} exceeds Bedrock's {bedrock.BATCH_MAX_RECORDS}-record "
            f"per-job quota."
        )

    vectors: list[Vector] = []
    spent = Decimal("0")
    for start in range(0, len(texts), job_size):
        chunk = texts[start:start + job_size]
        cost = gate.check(embedder.key, tokens_in=estimate_tokens(chunk))
        vectors.extend(embedder.embed(chunk))
        gate.record(cost)
        spent += cost
    return vectors, spent
