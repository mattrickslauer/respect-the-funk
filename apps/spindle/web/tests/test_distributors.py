"""The statement readers.

Every case here is a string literal, because parsing is pure by design — no database,
no network, no clock. The fixtures are synthetic: they carry the column names
DistroKid documents, with invented ISRCs and made-up numbers. No real statement,
artist or earning appears in this repository.

The assertions that matter are the refusals. A reader that cannot parse a file is a
visible problem; a reader that parses it *wrongly* writes plausible numbers into
`party_metric` and nobody finds out.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from spindle import distributors
from spindle.distributors import StatementError, base
from spindle.distributors.distrokid import CURRENT

TSV = "\t".join
DK_HEADER = TSV(["Reporting Date", "Sale Month", "Store", "Artist", "Title",
                 "ISRC", "UPC", "Country of Sale", "Quantity", "Earnings (USD)"])


def dk(*rows: str) -> str:
    return "\n".join([DK_HEADER, *rows]) + "\n"


ROW_A = TSV(["2026-03-01", "2026-01", "Spotify", "Amanda Kurt", "Slow Return",
             "QZABC2500001", "0602557123456", "DE", "12043", "38.11"])
ROW_B = TSV(["2026-03-01", "2026-01", "Apple Music", "Amanda Kurt", "Slow Return",
             "QZ-ABC-25-00001", "602557123456", "us", "3120", "$21.44"])


class Detection(unittest.TestCase):
    def test_detects_distrokid_from_its_header(self) -> None:
        self.assertEqual(distributors.detect(dk(ROW_A)).key, CURRENT.key)

    def test_empty_file_is_refused(self) -> None:
        with self.assertRaises(StatementError):
            distributors.detect("   \n")

    def test_unknown_header_names_the_columns_it_saw(self) -> None:
        with self.assertRaises(StatementError) as caught:
            distributors.parse("alpha,beta,gamma\n1,2,3\n")
        message = str(caught.exception)
        self.assertIn("alpha", message)
        self.assertIn("gamma", message)

    def test_a_comma_file_is_not_mistaken_for_a_tab_file(self) -> None:
        # The same column names, comma separated. DistroKid's reader requires tabs,
        # so this must refuse rather than read every row as one giant column.
        commas = dk(ROW_A).replace("\t", ",")
        with self.assertRaises(StatementError):
            distributors.parse(commas)

    def test_missing_a_required_column_refuses(self) -> None:
        # Drop ISRC. Without it a line cannot be attached to a recording, so a
        # reader that accepted this would produce metrics belonging to nothing.
        header = DK_HEADER.replace("\tISRC", "")
        row = "\t".join(c for i, c in enumerate(ROW_A.split("\t")) if i != 5)
        with self.assertRaises(StatementError):
            distributors.parse(f"{header}\n{row}\n")

    def test_no_two_formats_claim_the_same_required_columns(self) -> None:
        # An ambiguous registry makes `detect` pick arbitrarily between readers.
        seen: dict[tuple, str] = {}
        for fmt in distributors.FORMATS:
            key = (fmt.delimiter, tuple(sorted(fmt.required)))
            self.assertNotIn(key, seen,
                             f"{fmt.key} and {seen.get(key)} are indistinguishable")
            seen[key] = fmt.key


class Reading(unittest.TestCase):
    def test_reads_every_row(self) -> None:
        _, lines = distributors.parse(dk(ROW_A, ROW_B))
        self.assertEqual(len(lines), 2)

    def test_isrc_is_canonical_and_the_raw_form_is_kept(self) -> None:
        _, lines = distributors.parse(dk(ROW_A, ROW_B))
        # Hyphenated and bare spellings are one recording.
        self.assertEqual(lines[0].isrc, lines[1].isrc)
        self.assertEqual(lines[1].isrc_raw, "QZ-ABC-25-00001")

    def test_gtin_leading_zero_does_not_split_one_release_into_two(self) -> None:
        _, lines = distributors.parse(dk(ROW_A, ROW_B))
        self.assertEqual(lines[0].gtin, lines[1].gtin)
        self.assertEqual(lines[0].gtin, "00000602557123456"[-14:])

    def test_money_is_decimal_and_survives_a_currency_symbol(self) -> None:
        _, lines = distributors.parse(dk(ROW_B))
        self.assertEqual(lines[0].earnings, Decimal("21.44"))
        self.assertIsInstance(lines[0].earnings, Decimal)

    def test_quantity_is_an_int(self) -> None:
        _, lines = distributors.parse(dk(ROW_A))
        self.assertEqual(lines[0].quantity, 12043)

    def test_sale_month_becomes_a_whole_month_not_a_day(self) -> None:
        # The period a measurement covers has to be comparable to the next one.
        _, lines = distributors.parse(dk(ROW_A))
        self.assertEqual(lines[0].period_start, date(2026, 1, 1))
        self.assertEqual(lines[0].period_end, date(2026, 1, 31))

    def test_reporting_date_is_not_used_as_the_period(self) -> None:
        # Reporting Date is when the distributor paid; Sale Month is when the
        # stream happened. Using the first would file January's streams in March.
        _, lines = distributors.parse(dk(ROW_A))
        self.assertNotEqual(lines[0].period_start, date(2026, 3, 1))

    def test_territory_is_upper_cased_only_when_it_looks_like_a_code(self) -> None:
        _, lines = distributors.parse(dk(ROW_A, ROW_B))
        self.assertEqual(lines[0].territory, "DE")
        self.assertEqual(lines[1].territory, "US")

    def test_blank_trailing_lines_are_skipped(self) -> None:
        _, lines = distributors.parse(dk(ROW_A) + "\n\t\t\t\t\t\t\t\t\t\n")
        self.assertEqual(len(lines), 1)

    def test_row_number_points_at_the_file_line(self) -> None:
        _, lines = distributors.parse(dk(ROW_A, ROW_B))
        self.assertEqual([l.row_number for l in lines], [2, 3])


class Degradation(unittest.TestCase):
    """An unparseable ISRC costs its own value, never the row and never the file — a
    blank identifier is a real, storable answer. `quantity` and `earnings` are the
    opposite case: `party_metric` has no way to represent "unreadable" other than a
    number, and a silently written `0` is indistinguishable on screen from a real
    zero. So those two refuse the whole file rather than degrade a cell — see
    `ToInt`/`ToMoney` below and `tests/test_harvested.py` for the unit-level
    behaviour this exercises end to end.
    """

    def test_an_unparseable_isrc_leaves_the_row_readable(self) -> None:
        bad = ROW_A.replace("QZABC2500001", "not-an-isrc")
        _, lines = distributors.parse(dk(bad))
        self.assertEqual(lines[0].isrc, "")
        self.assertEqual(lines[0].isrc_raw, "not-an-isrc")
        self.assertEqual(lines[0].quantity, 12043)

    def test_an_unparseable_amount_refuses_the_whole_file(self) -> None:
        # Previously this silently wrote Decimal("0") — indistinguishable on screen
        # from a real zero-earnings row. `party_metric` has no "unknown" to fall
        # back to, so this must fail loudly rather than fabricate the number.
        bad = ROW_A.replace("38.11", "n/a")
        with self.assertRaises(base.StatementUnparseable) as caught:
            distributors.parse(dk(bad))
        self.assertEqual(caught.exception.column, "earnings")
        self.assertEqual(caught.exception.cell, "n/a")

    def test_an_unparseable_amount_is_still_a_statement_error(self) -> None:
        # So `statements.preview()`'s `except StatementError` turns this into a
        # clean `Report.refused` rather than an unhandled exception.
        bad = ROW_A.replace("38.11", "n/a")
        with self.assertRaises(StatementError):
            distributors.parse(dk(bad))

    def test_an_unparseable_quantity_refuses_the_whole_file(self) -> None:
        bad = ROW_A.replace("12043", "a lot")
        with self.assertRaises(base.StatementUnparseable) as caught:
            distributors.parse(dk(bad))
        self.assertEqual(caught.exception.column, "quantity")

    def test_an_unparseable_period_is_none_not_today(self) -> None:
        # Defaulting to now would disguise a missing period as a fresh measurement.
        bad = ROW_A.replace("\t2026-01\t", "\t\t")
        _, lines = distributors.parse(dk(bad))
        self.assertIsNone(lines[0].period_start)


class Currency(unittest.TestCase):
    """DistroKid's BANK breakdown has no currency column at all — every line used
    to be silently stamped `"USD"` by the reader, the same defect class as `unit`
    defaulting to `"count"`. Fixed by making `Format.currency` a required field the
    format states explicitly (DistroKid's own header names it: `Earnings (USD)`),
    with a real per-row `"currency"` column, when a format maps one, taking
    priority."""

    def test_a_format_with_no_currency_column_reports_its_own_stated_currency(self) -> None:
        _, lines = distributors.parse(dk(ROW_A))
        self.assertEqual(lines[0].currency, "USD")
        self.assertEqual(CURRENT.currency, "USD")

    def test_currency_is_a_required_field_not_a_default(self) -> None:
        # `Format` has no fallback for `currency` — omitting it is a TypeError at
        # construction, the same way a `Format` with no `key` would be.
        with self.assertRaises(TypeError):
            base.Format(  # type: ignore[call-arg]
                key="x", distributor="x", label="x", delimiter="\t",
                required=frozenset(), columns={},
            )


class Helpers(unittest.TestCase):
    def test_month_bounds_handles_december(self) -> None:
        self.assertEqual(base.month_bounds(date(2026, 12, 9)),
                         (date(2026, 12, 1), date(2026, 12, 31)))

    def test_month_bounds_handles_february_in_a_leap_year(self) -> None:
        self.assertEqual(base.month_bounds(date(2028, 2, 3))[1], date(2028, 2, 29))

    def test_header_normalisation_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(base.normalise_header("  Earnings (USD) "), "earnings usd")


if __name__ == "__main__":
    unittest.main()
