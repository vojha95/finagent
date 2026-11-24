"""Face-statement cells read in both channels, and the face-block sum (blueprint 8.9).

A RowInstance addresses its cells in the document grid (table_index, source_row, column
spans), and a TagFact carries the cell it sits in, so one visible cell can be read by the
normaliser (document channel) and checked against the tag in that cell. This module reads the
tags, so it lives outside finagent/document (rule 7).

face_block_sum: when a total is not presented (e.g. no total liabilities line), sum the presented
rows between two boundaries: after the anchor row (e.g. total current liabilities) up to the first
row whose label or parent header matches a stop pattern (commitments and contingencies,
temporary equity, the equity header). Rows without a value are skipped, and so is a subtotal: a
total-styled row (bold total or a label starting "Total") whose value equals the components
directly above it (a trailing run since the previous boundary). A total-styled row that does not foot is a component (filers set
single-line items such as "Long-Term Debt" in bold). Each component is the normaliser's value,
checked against the tag at that cell.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finagent.config import SignRule
from finagent.document.normalize import NormalisedValue, value_for_period
from finagent.ixbrl.tagstore import TagFact
from finagent.records import RowInstance
from finagent.verify import within_precision

CellKey = tuple[int, int]  # (table index, row index)

# Component and cell statuses (8.7 vocabulary).
VERIFIED = "VERIFIED"
DOCUMENT_ONLY = "SINGLE_CHANNEL"
DISAGREE = "DISAGREE"

# Face-block gap reasons.
NO_ANCHOR_ROW = "NO_ANCHOR_ROW"
NO_STOP_ROW = "NO_STOP_ROW"
EMPTY_BLOCK = "EMPTY_BLOCK"
STATEMENT_NOT_LOCATED = "STATEMENT_NOT_LOCATED"


def facts_by_cell(facts: Iterable[TagFact]) -> dict[CellKey, list[TagFact]]:
    """Non-nil facts grouped by the table row they sit in."""
    grouped: dict[CellKey, list[TagFact]] = defaultdict(list)
    for fact in facts:
        address = fact.cell_address
        if address is not None and not fact.is_nil and fact.amount is not None:
            grouped[(address.table_index, address.row_index)].append(fact)
    return grouped


def rows_by_cell(rows: Iterable[RowInstance]) -> dict[CellKey, RowInstance]:
    return {(row.table_index, row.source_row): row for row in rows}


def column_for(row: RowInstance, column_index: int) -> int | None:
    """The row column whose span holds a grid column index, or None."""
    for column, (first, last) in enumerate(row.column_spans):
        if first <= column_index <= last:
            return column
    return None


def cell_facts(
    row: RowInstance, period_end: date, by_cell: dict[CellKey, list[TagFact]]
) -> list[TagFact]:
    """Non-dimensional facts in the row's cell for the period ending period_end."""
    found = []
    for fact in by_cell.get((row.table_index, row.source_row), []):
        if fact.dimensions or fact.end != period_end:
            continue
        column = column_for(row, fact.cell_address.column_index)
        if column is not None and row.column_periods[column] == period_end:
            found.append(fact)
    return found


@dataclass(frozen=True)
class CellReading:
    """One visible cell in both channels."""

    row_id: str
    label: str
    document: NormalisedValue
    tag_amount: Decimal | None
    concept: str | None
    status: str  # VERIFIED, SINGLE_CHANNEL (document only) or DISAGREE

    @property
    def value(self) -> Decimal | None:
        """The value used in sums: the tag amount when the channels agree, the document value
        when there is no tag, None when they disagree."""
        if self.status == VERIFIED:
            return self.tag_amount
        if self.status == DOCUMENT_ONLY:
            return self.document.value
        return None


def read_cell(
    row: RowInstance, period_end: date, by_cell: dict[CellKey, list[TagFact]],
    sign: SignRule = "either",
) -> CellReading | None:
    """The row's value for the period, checked against the tag at that cell; None when the row
    shows no value for the period (blank, unreadable or no such column)."""
    document = value_for_period(row, period_end, sign)
    if document is None or document.value is None:
        return None
    facts = cell_facts(row, period_end, by_cell)
    for fact in facts:
        amount = abs(fact.amount) if sign == "positive" else fact.amount
        if within_precision(document.value, document.scale, amount):
            return CellReading(row.row_id, row.label, document, amount, fact.concept, VERIFIED)
    if facts:
        return CellReading(row.row_id, row.label, document, facts[0].amount, facts[0].concept,
                           DISAGREE)
    return CellReading(row.row_id, row.label, document, None, None, DOCUMENT_ONLY)


def _shown(reading: CellReading) -> Decimal:
    return reading.value if reading.value is not None else reading.document.value


def is_subtotal(row: RowInstance, reading: CellReading, segment: Sequence[CellReading]) -> bool:
    """A total-styled row equal to the components directly above it: some trailing run of the
    components since the last boundary, at displayed precision (filers foot displayed figures;
    a miss either way shows up as an I9 failure, never as a silent pass)."""
    if not segment or not (row.is_total or row.label.strip().lower().startswith("total")):
        return False
    shown = _shown(reading)
    unit = Decimal(5).scaleb((reading.document.scale or 0) - 1)
    total = Decimal(0)
    for count, component in enumerate(reversed(segment), start=1):
        total += _shown(component)
        if abs(shown - total) < unit:
            return True
    return False


def matches_stop(row: RowInstance, patterns: Sequence[re.Pattern]) -> bool:
    texts = [row.label.strip(), (row.parent_header or "").strip()]
    return any(p.search(text) for p in patterns for text in texts if text)


@dataclass(frozen=True)
class FaceBlock:
    """A face-block sum: components used, rows skipped, the stop row, or a gap reason."""

    components: tuple[CellReading, ...]
    skipped: tuple[str, ...]  # labels of subtotals and rows without a value for the period
    stop_row: str | None
    gap_reason: str | None

    @property
    def total(self) -> Decimal | None:
        """Sum of the component values; None for a gap or when a component disagrees."""
        if self.gap_reason is not None or any(c.value is None for c in self.components):
            return None
        return sum((c.value for c in self.components), Decimal(0))

    @property
    def status(self) -> str | None:
        """Weakest component status (DISAGREE < document only < VERIFIED)."""
        if self.gap_reason is not None:
            return None
        statuses = {c.status for c in self.components}
        for status in (DISAGREE, DOCUMENT_ONLY, VERIFIED):
            if status in statuses:
                return status
        return None


def face_block(
    rows: Sequence[RowInstance],
    anchor_row_id: str | None,
    period_end: date,
    stop_patterns: Sequence[re.Pattern],
    facts: Iterable[TagFact],
    include_anchor: bool = False,
) -> FaceBlock:
    """Components between the anchor row and the first stop row of one statement's rows.

    include_anchor adds the anchor row's own value first (e.g. total liabilities = total current
    liabilities + the rows below it); an anchor without a value for the period is a gap.
    """
    ordered = sorted(rows, key=lambda r: r.position)
    if not ordered:
        return FaceBlock((), (), None, STATEMENT_NOT_LOCATED)
    anchor = next((r for r in ordered if r.row_id == anchor_row_id), None)
    if anchor is None:
        return FaceBlock((), (), None, NO_ANCHOR_ROW)
    by_cell = facts_by_cell(facts)
    components: list[CellReading] = []
    skipped: list[str] = []
    if include_anchor:
        reading = read_cell(anchor, period_end, by_cell)
        if reading is None:
            return FaceBlock((), (), None, NO_ANCHOR_ROW)
        components.append(reading)
    segment: list[CellReading] = []  # components since the anchor or the last subtotal
    for row in ordered:
        if row.position <= anchor.position:
            continue
        if matches_stop(row, stop_patterns):
            if len(components) == int(include_anchor):
                return FaceBlock((), tuple(skipped), row.label, EMPTY_BLOCK)
            return FaceBlock(tuple(components), tuple(skipped), row.label, None)
        reading = read_cell(row, period_end, by_cell)
        if reading is None:
            skipped.append(row.label)
        elif is_subtotal(row, reading, segment):
            skipped.append(row.label)
            segment = []
        else:
            components.append(reading)
            segment.append(reading)
    return FaceBlock(tuple(components), tuple(skipped), None, NO_STOP_ROW)
