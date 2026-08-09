"""The embedding port. One interface, two providers, and the spend gate in front of both.

`PLATFORM-SPEC §8` assumes Bedrock is the embedding provider. On this account it cannot
be: Titan Text Embeddings V2 is `ACTIVE` and access is granted, but the on-demand quota
is **0 requests per minute**, so every invoke returns `ThrottlingException`. A quota
increase has been requested; it is not a thing to wait on.

So the provider is a port with two adapters, and which one runs is an environment
variable. That is not a hedge — it is the shape this should have had anyway, because the
one thing a vector store must never do is mix embeddings from two models in one index.

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
way to say where it came from.

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
from decimal import Decimal
from typing import Callable, Protocol, Sequence

from rtf_platform import spend

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
    """


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
    """Titan Text Embeddings V2, natively 1024.

    Unusable on this account until the quota case clears — kept live and tested so the
    swap is an environment variable rather than a rewrite under deadline. Titan embeds
    one text per invoke, so a batch is a loop; that is the API, not an oversight.
    """

    client: object  # boto3 bedrock-runtime; typed loosely to keep boto3 out of the zip
    key: str = "bedrock:amazon.titan-embed-text-v2:0"
    model: str = "bedrock:amazon.titan-embed-text-v2:0"

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            response = self.client.invoke_model(  # type: ignore[attr-defined]
                modelId="amazon.titan-embed-text-v2:0",
                body=json.dumps({"inputText": text, "dimensions": DIMENSIONS}),
            )
            body = json.loads(response["body"].read())
            out.append(Vector(list(body["embedding"]), self.model))
        return out


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
    """
    choice = os.environ.get("RTF_EMBED_PROVIDER", "").strip().lower()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if choice == "openai" or (not choice and openai_key):
        if not openai_key:
            raise EmbeddingUnavailable("RTF_EMBED_PROVIDER=openai but OPENAI_API_KEY is unset.")
        return OpenAIEmbedder(api_key=openai_key, post=post)

    if choice == "bedrock":
        try:
            import boto3  # noqa: PLC0415 — optional, and absent from the Lambda zip
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "RTF_EMBED_PROVIDER=bedrock but boto3 is not installed."
            ) from exc
        return BedrockEmbedder(client=boto3.client("bedrock-runtime"))

    raise EmbeddingUnavailable(
        "No embedding provider configured. Set OPENAI_API_KEY, or "
        "RTF_EMBED_PROVIDER=bedrock once the Titan quota case clears."
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
        vectors.extend(embedder.embed(chunk))
        gate.record(cost)
        spent += cost
    return vectors, spent
