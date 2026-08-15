"""The embedding port, tested with no network, no API key and no money.

    python -m unittest discover apps/spindle/web/tests

Same stdlib-only rule as `test_spend.py`, and the same reason: a guard nobody can run
cheaply is a guard nobody runs. Every provider call goes through an injected `post`, so
this suite is offline by construction rather than by mocking discipline.

The assertions concentrate on the two failure modes that do not announce themselves:
a vector of the wrong width, and vectors silently attributed to the wrong model. Both
produce a working-looking system that returns nonsense.
"""

from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest import mock

from spindle import embed, spend


def _env(**overrides: str):
    return mock.patch.dict(os.environ, overrides, clear=True)


def _vec(n: int = embed.DIMENSIONS, fill: float = 0.1) -> list[float]:
    return [fill] * n


def _open_gate() -> spend.Gate:
    with _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="1.00"):
        return spend.Gate.open(None, None)


class Vectors(unittest.TestCase):

    def test_wrong_width_is_refused_at_construction(self):
        # The schema column is VECTOR(1024). A 1536-wide vector inserts fine into a
        # list and fails at the database, far from the code that made the mistake.
        with self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embed.Vector(_vec(1536), "openai:text-embedding-3-small")
        self.assertIn("1536", str(caught.exception))
        self.assertIn("1024", str(caught.exception))

    def test_literal_is_a_cockroach_vector(self):
        v = embed.Vector([1.0, 2.5] + _vec(embed.DIMENSIONS - 2, 0.0), "m")
        literal = v.literal()
        self.assertTrue(literal.startswith("[1.0,2.5,"))
        self.assertTrue(literal.endswith("]"))
        self.assertEqual(literal.count(","), embed.DIMENSIONS - 1)

    def test_model_travels_with_the_values(self):
        v = embed.Vector(_vec(), "openai:text-embedding-3-small")
        self.assertEqual(v.model, "openai:text-embedding-3-small")


class OpenAI(unittest.TestCase):

    def _embedder(self, payload: dict) -> tuple[embed.OpenAIEmbedder, list]:
        seen: list = []

        def post(url, headers, body):
            seen.append((url, headers, body))
            return payload

        return embed.OpenAIEmbedder(api_key="sk-test", post=post), seen

    def test_asks_for_the_schema_width(self):
        # If this stops being sent, OpenAI returns its native 1536 and every insert
        # fails — but only once it reaches the database.
        embedder, seen = self._embedder(
            {"data": [{"index": 0, "embedding": _vec()}]}
        )
        embedder.embed(["hello"])
        _, headers, body = seen[0]
        self.assertEqual(body["dimensions"], 1024)
        self.assertEqual(body["input"], ["hello"])
        self.assertEqual(headers["authorization"], "Bearer sk-test")

    def test_results_are_ordered_by_index_not_by_arrival(self):
        # The API documents in-order returns. Trusting that silently misassigns every
        # vector if it ever changes, and a misassigned vector does not raise.
        embedder, _ = self._embedder({"data": [
            {"index": 1, "embedding": _vec(fill=0.2)},
            {"index": 0, "embedding": _vec(fill=0.1)},
        ]})
        first, second = embedder.embed(["a", "b"])
        self.assertEqual(first.values[0], 0.1)
        self.assertEqual(second.values[0], 0.2)

    def test_short_response_is_an_error_not_a_silent_truncation(self):
        embedder, _ = self._embedder({"data": [{"index": 0, "embedding": _vec()}]})
        with self.assertRaises(embed.EmbeddingUnavailable):
            embedder.embed(["a", "b", "c"])

    def test_malformed_response_is_an_error(self):
        embedder, _ = self._embedder({"error": {"message": "nope"}})
        with self.assertRaises(embed.EmbeddingUnavailable):
            embedder.embed(["a"])


class TokenEstimates(unittest.TestCase):

    def test_rounds_up_rather_than_down(self):
        # The gate needs a number before the call. Under-estimating is the direction
        # that spends money nobody approved.
        self.assertGreater(embed.estimate_tokens(["abcd"]), 1)

    def test_sums_across_texts(self):
        one = embed.estimate_tokens(["a" * 400])
        two = embed.estimate_tokens(["a" * 400, "a" * 400])
        self.assertEqual(two, one * 2)


class Loading(unittest.TestCase):

    def test_nothing_configured_says_so_clearly(self):
        with _env(), self.assertRaises(embed.EmbeddingUnavailable) as caught:
            embed.load()
        self.assertIn("No embedding provider configured", str(caught.exception))

    def test_openai_key_alone_is_enough(self):
        with _env(OPENAI_API_KEY="sk-test"):
            embedder = embed.load()
        self.assertIsInstance(embedder, embed.OpenAIEmbedder)
        self.assertEqual(embedder.model, "openai:text-embedding-3-small")

    def test_explicit_openai_without_a_key_is_an_error_not_a_fallback(self):
        with _env(RTF_EMBED_PROVIDER="openai"), self.assertRaises(embed.EmbeddingUnavailable):
            embed.load()

    def test_unknown_provider_does_not_silently_pick_one(self):
        with _env(RTF_EMBED_PROVIDER="cohere", OPENAI_API_KEY="sk-test"), \
                self.assertRaises(embed.EmbeddingUnavailable):
            embed.load()

    def test_every_provider_is_priced(self):
        # An adapter whose key is missing from the rate card cannot be called at all.
        # Catching that here is cheaper than catching it mid-backfill.
        for key in (embed.OpenAIEmbedder.key, embed.BedrockEmbedder.key):
            self.assertIn(key, spend.RATES, f"{key} has no rate card entry")


class Batching(unittest.TestCase):

    def _counting_embedder(self) -> tuple[embed.Embedder, list[int]]:
        sizes: list[int] = []

        class Counting:
            key = "openai:text-embedding-3-small"
            model = "openai:text-embedding-3-small"

            def embed(self, texts):
                sizes.append(len(texts))
                return [embed.Vector(_vec(), self.model) for _ in texts]

        return Counting(), sizes

    def test_empty_input_costs_nothing_and_calls_nothing(self):
        embedder, sizes = self._counting_embedder()
        vectors, cost = embed.embed_batch(_open_gate(), embedder, [])
        self.assertEqual(vectors, [])
        self.assertEqual(cost, Decimal("0"))
        self.assertEqual(sizes, [])

    def test_splits_into_batches(self):
        embedder, sizes = self._counting_embedder()
        vectors, _ = embed.embed_batch(_open_gate(), embedder, ["t"] * 40)
        self.assertEqual(len(vectors), 40)
        self.assertEqual(sizes, [16, 16, 8])

    #: Long enough that one batch costs more than a micro-dollar. `spend.estimate`
    #: quantizes to six places because the whole system stores `cost_micro_usd`, so a
    #: one-word input rounds to $0.000000 and the gate lets it through as free. That is
    #: correct — but it means a gate test written with short strings passes for the
    #: wrong reason, having never reached a policy check at all.
    LONG = "a" * 4000

    def test_closed_gate_refuses_before_any_provider_call(self):
        # Default-deny: an unset RTF_PAID_ENABLED must stop the call, not log it.
        embedder, sizes = self._counting_embedder()
        with _env():
            gate = spend.Gate.open(None, None)
        with self.assertRaises(spend.SpendRefused):
            embed.embed_batch(gate, embedder, [self.LONG])
        self.assertEqual(sizes, [], "provider was called despite a closed gate")

    def test_ceiling_stops_a_backfill_partway_and_keeps_what_it_paid_for(self):
        # One batch of 16 long texts costs ~$0.000321. A ceiling of $0.0005 affords
        # exactly one, so the second is refused.
        embedder, sizes = self._counting_embedder()
        with _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="0.0005"):
            gate = spend.Gate.open(None, None)
        with self.assertRaises(spend.SpendRefused):
            embed.embed_batch(gate, embedder, [self.LONG] * 64)
        # Some batches went through before the ceiling bit; the point is that it
        # stopped rather than either refusing everything or spending everything.
        self.assertEqual(sizes, [16])


if __name__ == "__main__":
    unittest.main()
