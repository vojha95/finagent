"""Per-table tagged-cell counts for statement location (blueprint 8.3 step 1; BC6).

The only tag-derived input the document channel may receive is {table_index: number of tagged
cells}: no concept names, values or facts. It is computed here, outside finagent/document, and
keyed by the clean document's table indices, which this module checks.
"""

from collections.abc import Iterable

from finagent.ixbrl.tagstore import TagFact, _local, parse_document, visible_tables


class TableAlignmentError(ValueError):
    """The tag store's tables and the clean document's tables do not correspond one to one."""


def tagged_table_counts(
    document: bytes, clean: bytes, facts: Iterable[TagFact]
) -> dict[int, int]:
    """Number of distinct tagged cells per table, keyed by clean-document table index.

    Nil facts are not counted (a nil fact displays no figure). Raises TableAlignmentError when
    the original's visible tables and the clean document's tables differ in number, or when a
    fact points at a table the clean document does not have.
    """
    visible = len(visible_tables(parse_document(document)))
    cleaned = sum(1 for e in parse_document(clean).iter() if _local(e) == "table")
    if visible != cleaned:
        raise TableAlignmentError(
            f"{visible} visible tables in the filing but {cleaned} in the clean document"
        )
    cells: set[tuple[int, int, int]] = set()
    for fact in facts:
        address = fact.cell_address
        if address is None or fact.is_nil:
            continue
        if not 0 <= address.table_index < cleaned:
            raise TableAlignmentError(
                f"fact {fact.fact_id} is in table {address.table_index}; the clean document "
                f"has {cleaned} tables"
            )
        cells.add((address.table_index, address.row_index, address.column_index))
    counts: dict[int, int] = {}
    for table_index, _, _ in cells:
        counts[table_index] = counts.get(table_index, 0) + 1
    return counts
