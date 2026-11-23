"""Phase 1 exit check: share of tagged statement cells that land in a built row (blueprint 15).

Takes plain (table, row, column) addresses, so the document package never imports the tag
store; evaluation code converts tag facts into addresses before calling it.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from finagent.records import RowInstance

CellAddress = tuple[int, int, int]  # (table index, row index, column index)


@dataclass(frozen=True)
class Coverage:
    covered: int
    total: int
    missed: tuple[CellAddress, ...]

    @property
    def share(self) -> float | None:
        """Covered / total, or None when there are no tagged cells to cover."""
        return self.covered / self.total if self.total else None


def covered_cells(rows: Iterable[RowInstance]) -> set[CellAddress]:
    """Every grid cell that falls inside a period column holding text in some row."""
    cells: set[CellAddress] = set()
    for row in rows:
        for (start, end), text in zip(row.column_spans, row.cell_texts):
            if text:
                cells.update((row.table_index, row.source_row, c) for c in range(start, end + 1))
    return cells


def cell_coverage(rows: Iterable[RowInstance], tagged: Iterable[CellAddress]) -> Coverage:
    """How many tagged cells in the statement tables are reached by a row's period column."""
    rows = list(rows)
    tables = {r.table_index for r in rows}
    targets = sorted({a for a in tagged if a[0] in tables})
    reached = covered_cells(rows)
    missed = tuple(a for a in targets if a not in reached)
    return Coverage(covered=len(targets) - len(missed), total=len(targets), missed=missed)
