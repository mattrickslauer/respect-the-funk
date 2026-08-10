"""Loading a distributor statement into the spine.

`distributors/` turns a file into rows and touches nothing. This turns those rows
into recordings and measurements, and it is the only part that writes.

Three decisions worth stating, because none of them is the obvious one.

**An import creates recordings.** A royalty statement is the label's own data: every
ISRC in it is a recording the label distributes, so learning the catalogue from it is
not a guess. That is also the point at which this becomes more than a stream counter
— the catalogue it seeds is what the ISRC probe fans out over, so importing a
statement is what makes the coverage grid have anything to show.

**It never invents a party.** The file carries an artist *name*, and a name is not an
identity. Names that match a roster party exactly get a credit; the rest are recorded
in `unmatched_artists` for a person to attribute. Creating a party from a string is
how a roster silently acquires three spellings of the same act.

**It refuses an unverified format unless told twice.** No reader here has been run
against a real export, so a column map may be wrong in a way that produces entirely
plausible numbers. A preview is always allowed; writing needs `allow_unverified`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg

from rtf_platform import distributors
from rtf_platform.distributors import Format, StatementError, StatementLine


@dataclass
class Report:
    """What an import did, or would do. The same shape either way, so a preview and
    the thing it previews cannot describe themselves differently."""
    format: Format | None = None
    rows_read: int = 0
    rows_loaded: int = 0
    rows_no_isrc: int = 0
    recordings_created: int = 0
    recordings_matched: int = 0
    metrics_written: int = 0
    unmatched_artists: list[str] = field(default_factory=list)
    stores: list[str] = field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    total_quantity: int = 0
    total_earnings: Decimal = Decimal("0")
    currency: str = "USD"
    duplicate_of: str = ""          # an import id, when this file was already loaded
    written: bool = False
    refused: str = ""               # why nothing was written

    @property
    def ok(self) -> bool:
        return not self.refused


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _slug(title: str, isrc: str) -> str:
    from rtf_platform.repo import slugify
    return slugify(title) or f"isrc-{isrc.lower()}"


def preview(text: str) -> Report:
    """Read the file and describe it. No database, so this is safe to run on
    anything an operator drops in, including a file from the wrong distributor."""
    report = Report()
    try:
        fmt, lines = distributors.parse(text)
    except StatementError as exc:
        report.refused = str(exc)
        return report

    report.format = fmt
    _summarise(report, lines)
    return report


def _summarise(report: Report, lines: list[StatementLine]) -> None:
    report.rows_read = len(lines)
    report.rows_no_isrc = sum(1 for line in lines if not line.isrc)
    report.total_quantity = sum(line.quantity for line in lines)
    report.total_earnings = sum((line.earnings for line in lines), Decimal("0"))
    report.stores = sorted({line.store for line in lines if line.store})
    starts = [line.period_start for line in lines if line.period_start]
    ends = [line.period_end for line in lines if line.period_end]
    report.period_start = min(starts) if starts else None
    report.period_end = max(ends) if ends else None
    currencies = {line.currency for line in lines if line.currency}
    report.currency = currencies.pop() if len(currencies) == 1 else "MIXED"


def load(
    conn: psycopg.Connection, tenant_id: str, text: str, *,
    filename: str = "", distributor: str = "", imported_by: str = "",
    allow_unverified: bool = False, dry_run: bool = False,
) -> Report:
    """Read the statement and, unless previewing, write what it says."""
    report = preview(text)
    if report.refused or report.format is None:
        return report

    fmt = report.format
    content_hash = digest(text)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM statement_import WHERE tenant_id = %s AND content_hash = %s",
            (tenant_id, content_hash),
        )
        existing = cur.fetchone()
    if existing:
        # Not an error. Uploading the same file twice is ordinary; doubling every
        # stream count because of it would not be.
        report.duplicate_of = str(existing["id"])
        report.refused = "This exact file has already been imported."
        return report

    if not fmt.verified and not allow_unverified:
        report.refused = (
            f"{fmt.label} has never been checked against a real export, so its "
            "columns may be mapped wrongly. Review the preview, then import again "
            "confirming you accept it.")
        return report

    if dry_run:
        return report

    _, lines = distributors.parse(text)
    usable = [line for line in lines if line.isrc]

    with conn.transaction():
        roster = _roster_by_name(conn, tenant_id)
        recordings = _ensure_recordings(conn, tenant_id, usable, report)
        _credit(conn, tenant_id, usable, recordings, roster, report)
        import_id = _record_import(
            conn, tenant_id, fmt, report,
            filename=filename, distributor=distributor or fmt.distributor,
            content_hash=content_hash, byte_size=len(text.encode("utf-8", "replace")),
            imported_by=imported_by,
        )
        report.metrics_written = _write_metrics(
            conn, tenant_id, usable, recordings, import_id)

    report.rows_loaded = len(usable)
    report.written = True
    return report


def _roster_by_name(conn: psycopg.Connection, tenant_id: str) -> dict[str, str]:
    """Roster parties keyed by casefolded name. Exact match only — a fuzzy one here
    would attribute a stranger's royalties to somebody on the roster."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.name FROM party p
                 JOIN party_role r ON r.tenant_id = p.tenant_id AND r.party_id = p.id
                                   AND r.role = 'roster_artist'
                WHERE p.tenant_id = %s""",
            (tenant_id,),
        )
        return {row["name"].strip().casefold(): str(row["id"]) for row in cur.fetchall()}


def _ensure_recordings(conn: psycopg.Connection, tenant_id: str,
                       lines: list[StatementLine], report: Report) -> dict[str, str]:
    """One recording per ISRC, created if the catalogue has not met it yet."""
    by_isrc: dict[str, StatementLine] = {}
    for line in lines:
        by_isrc.setdefault(line.isrc, line)

    found: dict[str, str] = {}
    with conn.cursor() as cur:
        for isrc, line in by_isrc.items():
            cur.execute(
                "SELECT id FROM recording WHERE tenant_id = %s AND isrc = %s",
                (tenant_id, isrc))
            row = cur.fetchone()
            if row:
                found[isrc] = str(row["id"])
                report.recordings_matched += 1
                continue
            # `slug` is unique per tenant and two different recordings can share a
            # title, so the ISRC breaks the tie rather than the insert failing.
            cur.execute(
                """INSERT INTO recording (tenant_id, slug, title, isrc, isrc_raw)
                   VALUES (%s, %s, %s, %s, %s)
              ON CONFLICT (tenant_id, slug) DO UPDATE SET slug = recording.slug
                RETURNING id, isrc""",
                (tenant_id, _slug(line.title, isrc), line.title or isrc,
                 isrc, line.isrc_raw),
            )
            made = cur.fetchone()
            if made["isrc"] != isrc:            # slug was taken by another recording
                cur.execute(
                    """INSERT INTO recording (tenant_id, slug, title, isrc, isrc_raw)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (tenant_id, f"{_slug(line.title, isrc)}-{isrc.lower()}",
                     line.title or isrc, isrc, line.isrc_raw))
                made = cur.fetchone()
            found[isrc] = str(made["id"])
            report.recordings_created += 1
    return found


def _credit(conn: psycopg.Connection, tenant_id: str, lines: list[StatementLine],
            recordings: dict[str, str], roster: dict[str, str],
            report: Report) -> None:
    unmatched: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for line in lines:
        name = line.artist.strip()
        if not name:
            continue
        party_id = roster.get(name.casefold())
        if party_id is None:
            unmatched.add(name)
            continue
        pairs.add((party_id, recordings[line.isrc]))

    with conn.cursor() as cur:
        for party_id, recording_id in sorted(pairs):
            cur.execute(
                """INSERT INTO party_credit
                        (tenant_id, party_id, subject_kind, subject_id, role, provenance)
                   VALUES (%s, %s, 'recording', %s, 'main_artist', 'measured')
              ON CONFLICT (tenant_id, party_id, subject_kind, subject_id, role)
                       DO NOTHING""",
                (tenant_id, party_id, recording_id))
    report.unmatched_artists = sorted(unmatched)


def _write_metrics(conn: psycopg.Connection, tenant_id: str,
                   lines: list[StatementLine], recordings: dict[str, str],
                   import_id: str) -> int:
    """Two measurements per line: how many plays, and how much money.

    They are separate rows with different units because they are different
    quantities, and `metric_one_per_period` means a corrected statement replaces a
    month rather than sitting beside the old answer with nothing to choose between
    them.
    """
    written = 0
    with conn.cursor() as cur:
        for line in lines:
            recording_id = recordings[line.isrc]
            for metric, value, unit in (
                ("streams", Decimal(line.quantity), "count"),
                ("earnings", line.earnings, line.currency.lower()),
            ):
                cur.execute(
                    """INSERT INTO party_metric
                            (tenant_id, recording_id, platform, entity_kind, metric,
                             value, unit, territory, period_start, period_end,
                             provenance, source, statement_import_id)
                       VALUES (%s, %s, %s, 'recording', %s, %s, %s, %s, %s, %s,
                               'measured', %s, %s)
                  ON CONFLICT (tenant_id, recording_id, platform, territory, metric,
                               period_start)
                       WHERE recording_id IS NOT NULL AND period_start IS NOT NULL
                       DO UPDATE SET value = excluded.value,
                                     unit = excluded.unit,
                                     period_end = excluded.period_end,
                                     statement_import_id = excluded.statement_import_id,
                                     observed_at = now()""",
                    (tenant_id, recording_id, line.store, metric, value, unit,
                     line.territory, line.period_start, line.period_end,
                     f"statement:{import_id}", import_id),
                )
                written += 1
    return written


def _record_import(conn: psycopg.Connection, tenant_id: str, fmt: Format,
                   report: Report, *, filename: str, distributor: str,
                   content_hash: str, byte_size: int, imported_by: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO statement_import
                    (tenant_id, distributor, format_key, format_verified, filename,
                     content_hash, byte_size, rows_read, rows_loaded, rows_no_isrc,
                     recordings_created, unmatched_artists, period_start, period_end,
                     total_quantity, total_earnings, currency, imported_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s)
            RETURNING id""",
            (tenant_id, distributor, fmt.key, fmt.verified, filename, content_hash,
             byte_size, report.rows_read, report.rows_read - report.rows_no_isrc,
             report.rows_no_isrc, report.recordings_created,
             report.unmatched_artists, report.period_start, report.period_end,
             report.total_quantity, report.total_earnings, report.currency,
             imported_by),
        )
        return str(cur.fetchone()["id"])


def recent(conn: psycopg.Connection, tenant_id: str,
           limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, distributor, format_key, format_verified, filename,
                      rows_read, rows_loaded, rows_no_isrc, recordings_created,
                      unmatched_artists, period_start, period_end, total_quantity,
                      total_earnings, currency, state, error, imported_by, created_at
                 FROM statement_import
                WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, limit),
        )
        return cur.fetchall()
