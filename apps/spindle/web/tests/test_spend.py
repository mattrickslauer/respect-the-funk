"""The spend gate, tested with no network, no database and no money.

`python -m unittest discover apps/spindle/web/tests`

Stdlib unittest rather than pytest on purpose: every dependency here is one somebody has
to install to check the thing that stops the bill running away, and a guard nobody can
run cheaply is a guard nobody runs. `infra/build.sh` prunes `tests/` from the Lambda
bundle, so this costs nothing at runtime either.

The assertions are mostly about *failure* modes, because that is where a spend gate earns
its keep. Any bug that makes a call go through when it should not is the expensive
direction, so every ambiguous input is tested for refusing.
"""

from __future__ import annotations

import os
import unittest
from decimal import Decimal
from unittest import mock

from spindle import spend


def _env(**overrides: str):
    """Replace the environment wholesale rather than patching keys.

    `clear=True` matters: a developer with RTF_PAID_ENABLED set in their shell would
    otherwise get a green run for a gate that is wide open.
    """
    return mock.patch.dict(os.environ, overrides, clear=True)


class Estimating(unittest.TestCase):

    def test_free_sources_cost_nothing(self):
        for key in ("musicbrainz", "web_page", "manual"):
            self.assertEqual(spend.estimate(key, tokens_in=10_000), Decimal("0"))

    def test_token_maths(self):
        # 1M in + 1M out of gpt-4o-mini at 0.15 / 0.60.
        cost = spend.estimate("openai:gpt-4o-mini",
                              tokens_in=1_000_000, tokens_out=1_000_000)
        self.assertEqual(cost, Decimal("0.750000"))

    def test_flat_per_request(self):
        self.assertEqual(
            spend.estimate("aws:ce:GetCostAndUsage", requests=3), Decimal("0.030000")
        )

    def test_unpriced_key_refuses_rather_than_guessing(self):
        with self.assertRaises(spend.SpendRefused) as caught:
            spend.estimate("openai:some-model-nobody-priced", tokens_in=1)
        self.assertEqual(caught.exception.reason, "unpriced")


class PolicyDefaults(unittest.TestCase):

    def test_empty_environment_is_fully_closed(self):
        with _env():
            policy = spend.Policy.load()
        self.assertFalse(policy.paid_enabled)
        self.assertEqual(policy.daily_ceiling_usd, Decimal("0"))

    def test_only_explicit_truthy_values_enable(self):
        for raw in ("1", "true", "TRUE", "yes"):
            with _env(RTF_PAID_ENABLED=raw):
                self.assertTrue(spend.Policy.load().paid_enabled, raw)
        # Everything else is off, including the ones that look affirmative.
        for raw in ("", "0", "false", "no", "on", "enabled", "y", "sure"):
            with _env(RTF_PAID_ENABLED=raw):
                self.assertFalse(spend.Policy.load().paid_enabled, raw)

    def test_malformed_ceiling_is_zero_not_default(self):
        for raw in ("5.00 USD", "abc", "$2", "1,000"):
            with _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD=raw):
                self.assertEqual(spend.Policy.load().daily_ceiling_usd, Decimal("0"), raw)

    def test_negative_ceiling_is_zero(self):
        with _env(RTF_DAILY_CEILING_USD="-10"):
            self.assertEqual(spend.Policy.load().daily_ceiling_usd, Decimal("0"))


class Gating(unittest.TestCase):

    def _gate(self, **env) -> spend.Gate:
        with _env(**env):
            # No connection and no tenant: the gate must work before there is a
            # database, or the first call of a cold start is ungated.
            return spend.Gate.open(None, None)

    def test_paid_call_refused_by_default(self):
        gate = self._gate()
        with self.assertRaises(spend.SpendRefused) as caught:
            gate.check("openai:gpt-4o-mini", tokens_in=1000, tokens_out=1000)
        self.assertEqual(caught.exception.reason, "disabled")
        self.assertEqual(len(gate.refused), 1)

    def test_free_calls_survive_the_kill_switch(self):
        # If disabling paid calls also broke MusicBrainz and page fetches, the kill
        # switch would be too expensive to use, which is how they end up never used.
        gate = self._gate()
        self.assertEqual(gate.check("musicbrainz"), Decimal("0"))
        self.assertEqual(gate.refused, [])

    def test_dry_run_refuses_before_the_enabled_check(self):
        gate = self._gate(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="10")
        gate.policy = spend.Policy(True, Decimal("10"), Decimal("1"), dry_run=True)
        with self.assertRaises(spend.SpendRefused) as caught:
            gate.check("openai:gpt-4o-mini", tokens_in=1000)
        self.assertEqual(caught.exception.reason, "dry_run")
        self.assertGreater(caught.exception.estimate_usd, 0)

    def test_per_call_ceiling(self):
        gate = self._gate(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="100",
                          RTF_PER_CALL_CEILING_USD="0.01")
        with self.assertRaises(spend.SpendRefused) as caught:
            gate.check("openai:gpt-4o-mini", tokens_in=1_000_000)
        self.assertEqual(caught.exception.reason, "per_call")

    def test_daily_ceiling_counts_what_was_already_spent(self):
        gate = self._gate(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="1.00",
                          RTF_PER_CALL_CEILING_USD="10")
        gate.already_spent_usd = Decimal("0.99")
        self.assertEqual(gate.remaining_usd, Decimal("0.01"))
        with self.assertRaises(spend.SpendRefused) as caught:
            gate.check("openai:gpt-4o-mini", tokens_in=1_000_000)   # $0.15
        self.assertEqual(caught.exception.reason, "daily")

    def test_allowed_call_returns_its_estimate_and_does_not_self_charge(self):
        gate = self._gate(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="1.00",
                          RTF_PER_CALL_CEILING_USD="1.00")
        cost = gate.check("openai:text-embedding-3-small", tokens_in=100_000)
        self.assertGreater(cost, 0)
        # check() must not move the meter — a call that was allowed and then failed on
        # the network cost nothing, and charging for it spends the ceiling on requests
        # that never billed.
        self.assertEqual(gate.already_spent_usd, Decimal("0"))
        gate.record(cost)
        self.assertEqual(gate.already_spent_usd, cost)

    def test_refusals_are_reportable(self):
        gate = self._gate()
        for _ in range(2):
            with self.assertRaises(spend.SpendRefused):
                gate.check("openai:gpt-4o-mini", tokens_in=1000)
        summary = gate.summary()
        self.assertFalse(summary["paid_enabled"])
        self.assertEqual(len(summary["refused"]), 2)
        self.assertEqual(summary["refused"][0]["reason"], "disabled")


class RateCard(unittest.TestCase):

    def test_cost_explorer_is_priced_so_nobody_polls_it(self):
        self.assertIn("aws:ce:GetCostAndUsage", spend.RATES)
        self.assertEqual(
            spend.RATES["aws:ce:GetCostAndUsage"].usd_per_request, Decimal("0.01")
        )

    def test_no_free_key_is_also_priced(self):
        # A key in both tables would resolve free and hide a real cost.
        self.assertEqual(spend.FREE & set(spend.RATES), set())


if __name__ == "__main__":
    unittest.main()
