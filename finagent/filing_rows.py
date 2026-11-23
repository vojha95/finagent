"""Statement rows for a real filing, located the same way on every path (blueprint 8.2-8.3; BC6).

Builds the clean document and the tag store, derives the {table index: tagged-cell count} map
(the only tag-derived input statement location may receive, rule 7) and builds the rows. Every
caller that reads a pinned filing (Phase 1 report, row dataset, E1) uses this, so location never
differs between them. It lives outside finagent/document, which may not read the tag store.
"""

from dataclasses import dataclass

from finagent.document import build_rows
from finagent.ixbrl import TagFact, build_tag_store, clean_document, tagged_table_counts
from finagent.records import RowInstance, Statement


@dataclass(frozen=True)
class FilingRows:
    clean: bytes
    facts: list[TagFact]
    tagged_cells: dict[int, int]  # clean-document table index -> tagged cells
    rows: dict[Statement, list[RowInstance]]  # a statement not located is absent (a gap)


def read_filing_rows(document: bytes, accession: str) -> FilingRows:
    """Clean document, tag store, tagged-cell map and statement rows for one filing.

    Raises TableAlignmentError when the tag store's tables and the clean document's differ.
    """
    clean = clean_document(document)
    facts = build_tag_store(document, accession)
    tagged_cells = tagged_table_counts(document, clean, facts)
    return FilingRows(clean, facts, tagged_cells, build_rows(clean, accession, tagged_cells))
