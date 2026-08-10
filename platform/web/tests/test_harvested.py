"""The parse boundary between an adapter's raw `dict` and the row it becomes.

Every one of these classes exists because a call site used to invent the most
confident answer it had for a field an adapter never actually supplied —
`provenance` defaulting to `measured`, `unit` defaulting to `count`, `release_type`
defaulting to `single`, `presence.mode` defaulting to `owned`, an unparseable
statement cell defaulting to `0`. `harvested.py` is where that stops being possible:
a required field with nothing to default to raises `HarvestInvalid`, naming the
adapter and the field, rather than inventing an answer.

Offline throughout — no database, no network, no clock.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from rtf_platform import harvested
from rtf_platform.distributors import StatementError
from rtf_platform.distributors.base import StatementUnparseable, _to_int, _to_money


# --------------------------------------------------------------------- Identifier

class Identifiers(unittest.TestCase):
    COMPLETE = {"kind": "spotify_artist", "value": "abc123", "provenance": "measured"}

    def test_accepts_a_complete_identifier(self) -> None:
        item = harvested.Identifier.parse(self.COMPLETE, adapter="spotify")
        self.assertEqual(item.kind, "spotify_artist")
        self.assertEqual(item.value, "abc123")
        self.assertEqual(item.provenance, "measured")
        # value_raw falls back to value itself — not a guess, the same identifier.
        self.assertEqual(item.value_raw, "abc123")

    def test_value_raw_is_kept_distinct_when_supplied(self) -> None:
        raw = {**self.COMPLETE, "value_raw": "ABC-123"}
        item = harvested.Identifier.parse(raw, adapter="spotify")
        self.assertEqual(item.value_raw, "ABC-123")

    def test_missing_kind_raises_naming_adapter_and_field(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "kind"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Identifier.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.adapter, "deezer")
        self.assertEqual(caught.exception.field, "kind")

    def test_missing_value_raises(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "value"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Identifier.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.field, "value")

    def test_missing_provenance_raises_rather_than_defaulting_to_measured(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "provenance"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Identifier.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.field, "provenance")

    def test_illegal_provenance_raises(self) -> None:
        raw = {**self.COMPLETE, "provenance": "guessed"}
        with self.assertRaises(harvested.HarvestInvalid):
            harvested.Identifier.parse(raw, adapter="deezer")


# --------------------------------------------------------------------------- Fact

class Facts(unittest.TestCase):
    COMPLETE = {"dimension": "genre", "value_text": "afrobeats",
                "provenance": "inferred", "confidence": 0.6}

    def test_accepts_a_complete_fact(self) -> None:
        item = harvested.Fact.parse(self.COMPLETE, adapter="spotify")
        self.assertEqual(item.dimension, "genre")
        self.assertEqual(item.value_text, "afrobeats")
        self.assertEqual(item.provenance, "inferred")
        self.assertEqual(item.confidence, 0.6)

    def test_confidence_is_genuinely_optional(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "confidence"}
        item = harvested.Fact.parse(raw, adapter="spotify")
        self.assertIsNone(item.confidence)

    def test_missing_dimension_raises(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "dimension"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Fact.parse(raw, adapter="spotify")
        self.assertEqual(caught.exception.field, "dimension")

    def test_missing_provenance_raises_rather_than_defaulting_to_inferred(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "provenance"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Fact.parse(raw, adapter="spotify")
        self.assertEqual(caught.exception.field, "provenance")

    def test_illegal_provenance_raises(self) -> None:
        raw = {**self.COMPLETE, "provenance": "vibes"}
        with self.assertRaises(harvested.HarvestInvalid):
            harvested.Fact.parse(raw, adapter="spotify")


# ------------------------------------------------------------------------- Metric

class Metrics(unittest.TestCase):
    COMPLETE = {"metric": "followers", "value": 4200.0, "unit": "count",
                "provenance": "measured"}

    def test_accepts_a_complete_metric(self) -> None:
        item = harvested.Metric.parse(self.COMPLETE, adapter="spotify")
        self.assertEqual(item.metric, "followers")
        self.assertEqual(item.value, 4200.0)
        self.assertEqual(item.unit, "count")
        self.assertEqual(item.provenance, "measured")

    def test_zero_is_a_real_value_not_a_missing_one(self) -> None:
        raw = {**self.COMPLETE, "value": 0.0}
        item = harvested.Metric.parse(raw, adapter="spotify")
        self.assertEqual(item.value, 0.0)

    def test_missing_unit_raises_rather_than_defaulting_to_count(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "unit"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Metric.parse(raw, adapter="spotify")
        self.assertEqual(caught.exception.field, "unit")

    def test_missing_provenance_raises(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "provenance"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Metric.parse(raw, adapter="spotify")
        self.assertEqual(caught.exception.field, "provenance")

    def test_missing_value_raises(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "value"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Metric.parse(raw, adapter="spotify")
        self.assertEqual(caught.exception.field, "value")

    def test_illegal_provenance_raises(self) -> None:
        raw = {**self.COMPLETE, "provenance": "vibes"}
        with self.assertRaises(harvested.HarvestInvalid):
            harvested.Metric.parse(raw, adapter="spotify")


# ---------------------------------------------------------------------- Recording

class Recordings(unittest.TestCase):
    def test_accepts_a_complete_recording(self) -> None:
        item = harvested.Recording.parse(
            {"title": "Slow Return", "isrc": "qz-abc-25-00001", "duration_ms": 210000},
            adapter="spotify")
        self.assertEqual(item.title, "Slow Return")
        self.assertEqual(item.isrc, "QZ-ABC-25-00001")
        self.assertEqual(item.duration_ms, 210000)

    def test_a_blank_title_is_a_real_answer_not_a_raise(self) -> None:
        # `recording` carries no provenance column; a title a platform did not give
        # is caught by the write side skipping it, not by this raising.
        item = harvested.Recording.parse({"isrc": "", "duration_ms": None},
                                         adapter="deezer")
        self.assertEqual(item.title, "")


# ------------------------------------------------------------------------ Release

class Releases(unittest.TestCase):
    COMPLETE = {"title": "Night Shift", "release_type": "single",
                "release_date": "2026-01-15", "gtin": "0602557123456"}

    def test_accepts_a_complete_release(self) -> None:
        item = harvested.Release.parse(self.COMPLETE, adapter="spotify")
        self.assertEqual(item.title, "Night Shift")
        self.assertEqual(item.release_type, "single")
        self.assertEqual(item.gtin, "0602557123456")

    def test_missing_release_type_raises_rather_than_defaulting_to_single(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "release_type"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Release.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.field, "release_type")
        self.assertEqual(caught.exception.adapter, "deezer")

    def test_blank_release_type_also_raises(self) -> None:
        raw = {**self.COMPLETE, "release_type": ""}
        with self.assertRaises(harvested.HarvestInvalid):
            harvested.Release.parse(raw, adapter="deezer")


# ----------------------------------------------------------------------- Presence

class Presences(unittest.TestCase):
    COMPLETE = {"platform": "deezer", "mode": "owned",
                "label": "Amanda Kurt", "url": "https://deezer.com/artist/1"}

    def test_accepts_a_complete_presence(self) -> None:
        item = harvested.Presence.parse(self.COMPLETE, adapter="deezer")
        self.assertEqual(item.platform, "deezer")
        self.assertEqual(item.mode, "owned")
        self.assertEqual(item.handle, "Amanda Kurt")
        self.assertEqual(item.profile_url, "https://deezer.com/artist/1")

    def test_missing_mode_raises_rather_than_defaulting_to_owned(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "mode"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Presence.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.field, "mode")

    def test_illegal_mode_raises(self) -> None:
        raw = {**self.COMPLETE, "mode": "sort-of"}
        with self.assertRaises(harvested.HarvestInvalid):
            harvested.Presence.parse(raw, adapter="deezer")

    def test_missing_platform_raises(self) -> None:
        raw = {k: v for k, v in self.COMPLETE.items() if k != "platform"}
        with self.assertRaises(harvested.HarvestInvalid) as caught:
            harvested.Presence.parse(raw, adapter="deezer")
        self.assertEqual(caught.exception.field, "platform")


# ------------------------------------------------------------- _to_int / _to_money

class ToInt(unittest.TestCase):
    def test_plain_integer(self) -> None:
        self.assertEqual(_to_int("12043"), 12043)

    def test_thousands_separator(self) -> None:
        self.assertEqual(_to_int("12,043"), 12043)

    def test_parenthesised_negative(self) -> None:
        self.assertEqual(_to_int("(120)"), -120)

    def test_leading_minus(self) -> None:
        self.assertEqual(_to_int("-120"), -120)

    def test_zero_is_not_unparseable(self) -> None:
        self.assertEqual(_to_int("0"), 0)

    def test_unparseable_raises(self) -> None:
        with self.assertRaises(StatementUnparseable) as caught:
            _to_int("n/a")
        self.assertEqual(caught.exception.cell, "n/a")

    def test_blank_raises(self) -> None:
        with self.assertRaises(StatementUnparseable):
            _to_int("")

    def test_unparseable_is_a_statement_error(self) -> None:
        # So `preview()`'s `except StatementError` still turns this into a clean
        # refusal instead of an unhandled exception reaching the web layer.
        with self.assertRaises(StatementError):
            _to_int("garbage")


class ToMoney(unittest.TestCase):
    def test_plain_decimal(self) -> None:
        self.assertEqual(_to_money("38.11"), Decimal("38.11"))
        self.assertIsInstance(_to_money("38.11"), Decimal)

    def test_currency_symbol(self) -> None:
        self.assertEqual(_to_money("$21.44"), Decimal("21.44"))

    def test_thousands_separator(self) -> None:
        self.assertEqual(_to_money("$1,234.56"), Decimal("1234.56"))

    def test_parenthesised_negative(self) -> None:
        self.assertEqual(_to_money("(12.34)"), Decimal("-12.34"))

    def test_leading_minus(self) -> None:
        self.assertEqual(_to_money("-12.34"), Decimal("-12.34"))

    def test_zero_is_not_unparseable(self) -> None:
        self.assertEqual(_to_money("0.00"), Decimal("0.00"))

    def test_unparseable_raises(self) -> None:
        with self.assertRaises(StatementUnparseable) as caught:
            _to_money("n/a")
        self.assertEqual(caught.exception.cell, "n/a")

    def test_blank_raises(self) -> None:
        with self.assertRaises(StatementUnparseable):
            _to_money("")


if __name__ == "__main__":
    unittest.main()
