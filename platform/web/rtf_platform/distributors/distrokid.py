"""DistroKid.

No API, and there never has been one. What exists is a **.tsv you download from the
BANK page** ("See Breakdown by Store") and a wider `.csv` from the "excruciating
details" page — so this module is a reader, not a fetcher, and no amount of design
makes it otherwise. Scraping the dashboard would put the label's own account at risk
for data the label can simply export, which is not a trade worth making.

**One format here, and a known gap.** DistroKid changed its report shape in July
2025, and third-party importers now ship a "legacy (pre-July 2025)" reader beside the
current one — so a second `Format` almost certainly belongs in this file. It is not
here because nobody has put a legacy file in front of this code, and two entries with
identical `required` columns would be worse than one: they would match the same file,
make detection ambiguous, and imply a coverage that does not exist. When a
pre-July-2025 export turns up, add it with the columns that actually differ.

The column names below come from DistroKid's help pages and from what importers
document consuming. **The format is not marked `verified`**, because nothing here has
been run against a real export. That flag is load-bearing rather than decorative:
`statements.load` refuses to write metrics from an unverified format unless it is
explicitly told to, so a plausible mis-parse cannot quietly become a number somebody
trusts. Confirming it is a one-line change once a real file has been through it.
"""

from __future__ import annotations

from rtf_platform.distributors.base import Format

#: `Reporting Date` and `Sale Month` are separate columns: the first is when
#: DistroKid was paid, the second is when the stream happened. Only the second is a
#: period a measurement belongs to, so only the second is mapped.
CURRENT = Format(
    key="distrokid.bank-tsv",
    distributor="distrokid",
    label="DistroKid — BANK breakdown (TSV)",
    delimiter="\t",
    required=frozenset({"sale month", "store", "isrc", "quantity"}),
    columns={
        "period": "Sale Month",
        "store": "Store",
        "artist": "Artist",
        "title": "Title",
        "isrc": "ISRC",
        "gtin": "UPC",
        "territory": "Country of Sale",
        "quantity": "Quantity",
        "earnings": "Earnings (USD)",
    },
    notes="BANK page → See Breakdown by Store → download. Tab separated. "
          "A pre-July-2025 export needs its own Format; see the module docstring.",
    verified=False,
)

FORMATS = (CURRENT,)
