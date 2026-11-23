"""Statement rows for a real filing, located the same way on every path (blueprint 8.2-8.3; BC6).

Builds the clean document and the tag store, derives the {table index: tagged-cell count} map
(the only tag-derived input statement location may receive, rule 7) and builds the rows. Every
caller that reads a pinned filing (Phase 1 report, row dataset, E1) uses this, so location never
differs between them. It lives outside finagent/document, which may not read the tag store.
The filing's fiscal periods ({fiscal year: period end}, from the evidence store's submissions
metadata, 8.5) are passed through the same way, to date bare-year columns.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from finagent.document import build_rows
from finagent.ixbrl import TagFact, build_tag_store, clean_document, tagged_table_counts
from finagent.records import RowInstance, Statement


@dataclass(frozen=True)
class FilingRows:
    clean: bytes
    facts: list[TagFact]
    tagged_cells: dict[int, int]  # clean-document table index -> tagged cells
    rows: dict[Statement, list[RowInstance]]  # a statement not located is absent (a gap)


# A column date within half a year of a fiscal period end belongs to that fiscal year.
_SAME_YEAR_DAYS = 183


def read_filing_rows(
    document: bytes, accession: str, fiscal_periods: Mapping[int, date] | None = None
) -> FilingRows:
    """Clean document, tag store, tagged-cell map and statement rows for one filing.

    fiscal_periods ({fiscal year: period end}, EvidenceStore.fiscal_periods) dates bare-year
    columns the document leaves undated. Raises TableAlignmentError when the tag store's tables
    and the clean document's differ.
    """
    clean = clean_document(document)
    facts = build_tag_store(document, accession)
    tagged_cells = tagged_table_counts(document, clean, facts)
    rows = build_rows(clean, accession, tagged_cells, fiscal_periods)
    return FilingRows(clean, facts, tagged_cells, rows)


def period_disagreements(
    rows: Iterable[RowInstance], fiscal_periods: Mapping[int, date]
) -> list[tuple[str, date, date]]:
    """Column dates the document states that differ from the filing's fiscal period end.

    Each dated amount column is matched to the fiscal period end nearest to it; a date within
    half a year of that end but not equal to it is a disagreement: (row_id, document date,
    fiscal period end). Expected to be empty.
    """
    ends = sorted(fiscal_periods.values())
    found = []
    for row in rows:
        for period, kind in zip(row.column_periods, row.column_kinds):
            if period is None or kind != "amount" or not ends:
                continue
            nearest = min(ends, key=lambda end: abs((end - period).days))
            if nearest != period and abs((nearest - period).days) <= _SAME_YEAR_DAYS:
                found.append((row.row_id, period, nearest))
    return found
