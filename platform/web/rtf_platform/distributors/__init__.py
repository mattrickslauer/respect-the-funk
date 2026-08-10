"""The distributor registry.

Adding a distributor is a `Format` in a module and one line in `FORMATS`. There is no
base class to subclass and no method to override, because every statement seen so far
is a delimited file with differently-spelled columns — and a plugin architecture for
a problem that is really a lookup table would cost more than it returns.

Detection reads the header and nothing else. That matters more than it sounds: the
dangerous failure is not refusing a file, it is reading one *wrongly* and writing
plausible numbers into `party_metric` that nobody can tell are wrong. So:

  * a format matches only when every column in `required` is present;
  * two formats matching equally well is an error, not a coin toss;
  * no match names the columns that were actually found, so the fix is mechanical.

Only DistroKid ships today. TuneCore, CD Baby and Symphonic each need one real export
to define, and writing their column maps from memory would be inventing the thing
this module exists to be careful about.
"""

from __future__ import annotations

from rtf_platform.distributors import distrokid
from rtf_platform.distributors.base import (
    Format, StatementError, StatementLine, StatementUnparseable, normalise_header, read,
)

#: Every readable statement shape. Order is irrelevant — detection scores.
FORMATS: tuple[Format, ...] = (
    *distrokid.FORMATS,
)

#: Distributors the product knows exist, whether or not it can read them yet. Shown
#: in the import UI so "we cannot read TuneCore" is a visible gap rather than a
#: silent absence — a dropdown listing only DistroKid reads as a product decision.
KNOWN_DISTRIBUTORS: tuple[tuple[str, str], ...] = (
    ("distrokid", "DistroKid"),
    ("tunecore", "TuneCore"),
    ("cdbaby", "CD Baby"),
    ("symphonic", "Symphonic"),
    ("unitedmasters", "UnitedMasters"),
    ("amuse", "Amuse"),
    ("routenote", "RouteNote"),
    ("other", "Other / unknown"),
)


def header_of(text: str, delimiter: str) -> tuple[str, ...]:
    line = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return tuple(normalise_header(c) for c in line.split(delimiter))


def detect(text: str) -> Format:
    """The one format that can read this file.

    Raises `StatementError` when none matches, and also when two match equally well:
    an ambiguous header means the registry has drifted, and picking arbitrarily
    between two readers is how a file gets parsed by the wrong one.
    """
    if not text.strip():
        raise StatementError("The file is empty.")

    scored: list[tuple[int, Format]] = []
    for fmt in FORMATS:
        columns = set(header_of(text, fmt.delimiter))
        if fmt.required <= columns:
            scored.append((len(fmt.required), fmt))

    if not scored:
        # Report the header under whichever delimiter split it into most fields —
        # otherwise a comma file read with tabs reports one enormous column.
        seen = max((header_of(text, d) for d in ("\t", ",", ";")), key=len)
        raise StatementError(
            "No distributor format matches this file. Columns found: "
            + ", ".join(c for c in seen if c) + ".",
            header=seen,
        )

    best = max(score for score, _ in scored)
    winners = [fmt for score, fmt in scored if score == best]
    if len(winners) > 1:
        raise StatementError(
            "This file matches more than one format equally well ("
            + ", ".join(f.key for f in winners)
            + "). Two readers cannot both be right — narrow their required columns.")
    return winners[0]


def parse(text: str) -> tuple[Format, list[StatementLine]]:
    """Detect and read in one call. Pure: no database, no network, no clock."""
    fmt = detect(text)
    return fmt, list(read(text, fmt))


__all__ = [
    "FORMATS", "KNOWN_DISTRIBUTORS", "Format", "StatementError", "StatementLine",
    "StatementUnparseable", "detect", "parse", "read",
]
