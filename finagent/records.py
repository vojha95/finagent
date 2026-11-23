"""Shared records and the outcome vocabulary (blueprint sections 6.1 and 13)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

# Blueprint 6.1 outcome statuses.
Status = Literal[
    "VERIFIED",
    "CORRECTED",
    "REVIEW",
    "MISSING_PRIMARY",
    "CONFLICT",
    "INVALID",
    "GAP",
    "EVIDENCE_GAP",
    "SKIPPED",
    "CLARIFICATION",
    "COMPLETE",
    "PARTIAL",
    # Blueprint 8.7 dual-channel results.
    "DISAGREE",
    "SINGLE_CHANNEL",
]

Channel = Literal["document", "tag"]

# Where a tag-channel reading came from. Both views are the filer's own tags (8.6 naming note).
TagSource = Literal["companyfacts", "inline", "inline_only"]


@dataclass(frozen=True)
class Reading:
    """One reading of a line item for one filing (blueprint 13, plus provenance fields)."""

    issuer: str
    fiscal_year: int
    item: str
    value: Decimal | None  # None means absent; never 0 for a missing value
    channel: Channel
    status: Status
    concept: str | None = None
    row_id: str | None = None
    unit: str | None = None
    start: date | None = None
    end: date | None = None
    accession: str | None = None
    source: TagSource | None = None
    single_source: bool = False


@dataclass(frozen=True)
class Outcome:
    """Result of a tool step (blueprint 13). reason is a stable code; detail is free text."""

    status: Status
    reason: str
    observations: tuple[Reading, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def value(self) -> Decimal | None:
        """The single value carried by the outcome, or None when there is not exactly one."""
        values = {r.value for r in self.observations if r.value is not None}
        return values.pop() if len(values) == 1 else None


Statement = Literal["IS", "BS", "CF"]
# A row column: a period amount, or a percentage sub-column that is never an amount (BC11).
ColumnKind = Literal["amount", "percent"]


@dataclass(frozen=True)
class RowInstance:
    """One row of a face statement in the clean document (blueprint 8.3, 13).

    cell_texts, column_periods, column_spans and column_kinds are aligned: one entry per
    column. A column is a period column ("amount") or a percentage sub-column ("percent",
    e.g. "% Sales" next to each year; BC11): a percent column has period None and is never read
    as an amount. An empty column_kinds means every column is an amount column. A cell with no visible figure is "" (missing is never zero). table_index, source_row and
    column_spans address the row in the document grid, so evaluation can compare it with the
    tag store without the document channel ever seeing a tag.
    """

    row_id: str
    accession: str
    statement: Statement
    label: str  # "" for an unlabelled row (e.g. a total line whose label cell is blank)
    parent_header: str | None
    indent: int  # indentation level within the statement, 0 = outermost
    is_total: bool
    is_bold: bool
    position: int  # order among the statement's value rows, from 0
    cell_texts: tuple[str, ...]
    column_periods: tuple[date | None, ...]
    unit_text: str | None
    table_index: int
    source_row: int
    column_spans: tuple[tuple[int, int], ...]
    column_kinds: tuple[ColumnKind, ...] = ()

    def is_amount_column(self, column: int) -> bool:
        """True for a period column; False for a percentage sub-column (BC11)."""
        return not self.column_kinds or self.column_kinds[column] == "amount"


@dataclass(frozen=True)
class Prediction:
    """A classifier's top candidates for one row (blueprint 8.4, 13), most probable first."""

    row_id: str
    candidates: tuple[tuple[str, float], ...]  # (canonical item or OTHER, probability)
    model_version: str

    @property
    def item(self) -> str:
        return self.candidates[0][0]
