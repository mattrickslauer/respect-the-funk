"""The contract every distributor statement is read through.

A distributor is not a source in the `source_manifest` sense. Nothing here fetches:
DistroKid has no public API, and the ones that do gate it behind an account nobody
can hand out. What arrives is a **file the label exported**, and this package turns
that file into rows.

Three things follow from that, and they shape the whole design.

**A distributor is not a format.** DistroKid changed its report shape in July 2025
and every third-party importer now carries a "legacy" reader beside the current one.
So the registry key is `(distributor, format)`, and which one applies is decided by
looking at the header rather than by asking the operator to know.

**Detection is by required columns, and a miss is an error.** The failure that
matters is not "we could not read the file" — it is reading it wrong and writing
plausible numbers into `party_metric` that nobody can tell are wrong. So a format
matches only when every column it needs is present, and an unmatched header raises
with the columns it actually saw.

**Parsing is pure.** No database, no network, no clock. A `Format` is data and
`parse` is a function over text, which is why the whole package is testable from a
string literal and why adding a distributor is a table entry rather than a module.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterator

from rtf_platform.domain import canonical_gtin, canonical_isrc


class StatementError(Exception):
    """The file could not be read as any known statement. Carries what was seen, so
    adding the missing format is a matter of reading the message."""

    def __init__(self, message: str, header: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.header = header


class StatementUnparseable(StatementError):
    """One cell could not be read as the type its column promises. A subclass of
    `StatementError` rather than a sibling: `statements.preview()`/`load()` already
    turn any `StatementError` into a clean `Report.refused`, and the failure mode
    this exists to kill — a garbled quantity or earnings cell silently becoming `0`
    and being written to `party_metric` as `measured` — deserves exactly that
    treatment, not an unhandled exception reaching the web layer. Carries the
    column and the raw cell, because the message a human needs is "which cell".
    """

    def __init__(self, column: str, cell: str) -> None:
        self.column = column
        self.cell = cell
        super().__init__(
            f"the {column!r} column holds {cell!r}, which is not a number a "
            "statement can carry.")


@dataclass(frozen=True)
class StatementLine:
    """One reported line: a recording, on a store, in a territory, over a period.

    `quantity` and `earnings` are kept apart and never added together — one is a
    count and the other is money, and the unit rule that keeps Spotify's popularity
    index out of a sum applies here too.
    """
    isrc: str                       # canonical, "" when the file's value was not one
    isrc_raw: str
    gtin: str
    title: str
    artist: str
    store: str                      # as the distributor spells it
    territory: str                  # ISO-3166-1 alpha-2 where recognisable
    period_start: date | None
    period_end: date | None
    quantity: int
    earnings: Decimal
    currency: str
    row_number: int                 # 1-based line in the file, for the error report


@dataclass(frozen=True)
class Format:
    """One readable shape of one distributor's statement.

    `columns` maps our field name to theirs. `required` is the subset that must all
    be present for this format to claim the file — keep it to the columns that
    identify the shape, not every column used.
    """
    key: str                        # "distrokid.2025-07"
    distributor: str                # "distrokid"
    label: str                      # what a human sees in the import log
    delimiter: str
    required: frozenset[str]
    columns: dict[str, str]
    #: Per-field converters, applied to the raw cell. Anything absent falls through
    #: to the default for that field.
    convert: dict[str, Callable[[str], object]] = field(default_factory=dict)
    notes: str = ""
    verified: bool = False          # against a real export, not a documented guess


def normalise_header(name: str) -> str:
    """Header cells differ by case, spacing and punctuation between exports of the
    same format. Comparing them normalised means a column renamed from `Sale Month`
    to `sale_month` does not read as a new format."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").strip().lower()).strip()


#: A distributor spells a negative in accounting notation — `(120)` — as often as
#: with a leading sign, and a naive digit-strip loses the parentheses along with the
#: sign they carry.
_PARENTHESISED = re.compile(r"^\(.*\)$")


def _to_int(raw: str, *, column: str = "quantity") -> int:
    """A quantity, tolerant of the thousands separators real statements use.

    Raises `StatementUnparseable` rather than returning 0 on anything that is not
    genuinely a number: `0` is a real, reportable quantity, and a cell that cannot
    be read at all must not be indistinguishable from one that reads as zero.
    """
    text = (raw or "").strip()
    negative = bool(_PARENTHESISED.match(text)) or text.startswith("-")
    cleaned = re.sub(r"[^0-9]", "", text)
    if not cleaned:
        raise StatementUnparseable(column, raw)
    value = int(cleaned)
    return -value if negative else value


def _to_money(raw: str, *, column: str = "earnings") -> Decimal:
    """Money as Decimal, never float. A statement summed as float drifts, and the
    one number nobody should have to argue about is the money.

    Tolerant of the formats a real export uses — a currency symbol, a thousands
    separator, parenthesised negatives — but raises `StatementUnparseable` on
    anything else rather than returning 0. Writing 0 for an unreadable cell is how
    real revenue silently disappears from `party_metric` as a `measured` fact; the
    label's own statement is exactly the file this must never happen to.
    """
    text = (raw or "").strip()
    negative = bool(_PARENTHESISED.match(text)) or text.startswith("-")
    cleaned = re.sub(r"[^0-9.]", "", text)
    if cleaned in ("", "."):
        raise StatementUnparseable(column, raw)
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise StatementUnparseable(column, raw) from None
    return -value if negative else value


#: Formats seen in the wild. `%Y-%m` first because a statement period is usually a
#: month, and a bare month must not be read as a day.
_DATE_FORMATS = ("%Y-%m", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %Y", "%B %Y")


def to_date(raw: str) -> date | None:
    """A date, or None. None is a real answer — a statement with no period is still
    a statement, and inventing today's date would make it look like a measurement
    taken now."""
    from datetime import datetime

    text = (raw or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def month_bounds(day: date | None) -> tuple[date | None, date | None]:
    """The month a reported date falls in. Distributors report per month, and a
    period with a start but no end cannot be compared to the next one."""
    if day is None:
        return None, None
    start = day.replace(day=1)
    end_month = start.replace(year=start.year + 1, month=1) if start.month == 12 \
        else start.replace(month=start.month + 1)
    return start, date.fromordinal(end_month.toordinal() - 1)


_TERRITORY = re.compile(r"^[A-Za-z]{2}$")


def _territory(raw: str) -> str:
    text = (raw or "").strip()
    return text.upper() if _TERRITORY.match(text) else text[:64]


def read(text: str, fmt: Format) -> Iterator[StatementLine]:
    """Rows from `text`, read as `fmt`. Assumes the format already matched."""
    reader = csv.DictReader(io.StringIO(text), delimiter=fmt.delimiter)
    lookup = {normalise_header(name): name for name in (reader.fieldnames or [])}

    def cell(row: dict[str, str], our: str) -> str:
        their = fmt.columns.get(our)
        if their is None:
            return ""
        actual = lookup.get(normalise_header(their))
        return (row.get(actual) or "").strip() if actual else ""

    for number, row in enumerate(reader, start=2):   # 1 is the header
        if not any((v or "").strip() for v in row.values()):
            continue                                  # trailing blank lines
        raw_isrc = cell(row, "isrc")
        reported = to_date(cell(row, "period"))
        start, end = month_bounds(reported)
        yield StatementLine(
            isrc=canonical_isrc(raw_isrc),
            isrc_raw=raw_isrc,
            gtin=canonical_gtin(cell(row, "gtin")),
            title=cell(row, "title"),
            artist=cell(row, "artist"),
            store=cell(row, "store"),
            territory=_territory(cell(row, "territory")),
            period_start=start,
            period_end=end,
            quantity=_to_int(cell(row, "quantity")),
            earnings=_to_money(cell(row, "earnings")),
            currency=(cell(row, "currency") or "USD").upper()[:3],
            row_number=number,
        )
