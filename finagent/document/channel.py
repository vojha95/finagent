"""Document channel: one reading per line item per filing, from rows and predictions (8.3-8.5).

The row predicted as an item is read for the filing's own period column and normalised. Rows
in the item's primary statement come first, then higher probability, then earlier position;
the other candidate rows are kept as alternatives for the Correlation Agent's re-pick (8.8).
Inputs are visible rows only; no tags (rule 7).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.document.normalize import value_for_period
from finagent.evidence.store import FilingRecord
from finagent.records import DocumentEdit, Prediction, RowInstance

FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
NO_PERIOD_COLUMN = "NO_PERIOD_COLUMN"


@dataclass(frozen=True)
class DocumentReading:
    item: str
    period_end: date
    value: Decimal | None  # None is a gap, never 0
    reason: str  # FOUND, or why there is no value (NOT_FOUND, BLANK, NO_PERIOD_COLUMN, ...)
    row_id: str | None = None
    statement: str | None = None
    probability: float | None = None
    scale: int | None = None
    sign_flipped: bool = False
    alternatives: tuple[str, ...] = ()
    # An in-memory edit (Correlation Agent correction or E2 injection; finagent.verify.edits):
    # the edit and the reading before it. None for the channel's own reading.
    edit: DocumentEdit | None = None
    original_value: Decimal | None = None
    original_row_id: str | None = None


def primary_statement(spec: LineItemSpec) -> str:
    """The first statement listed for the item ("IS, CF" -> "IS")."""
    return spec.statement.split(",")[0].strip()


def read_document_channel(
    rows: Iterable[RowInstance],
    predictions: Iterable[Prediction],
    filing: FilingRecord,
    specs: Iterable[LineItemSpec],
) -> dict[str, DocumentReading]:
    """One DocumentReading per item in specs, for the filing's own fiscal period."""
    by_id = {row.row_id: row for row in rows}
    candidates: dict[str, list[tuple[RowInstance, float]]] = {}
    for prediction in predictions:
        item, probability = prediction.candidates[0]
        if prediction.row_id in by_id:
            candidates.setdefault(item, []).append((by_id[prediction.row_id], probability))

    readings: dict[str, DocumentReading] = {}
    for spec in specs:
        found = candidates.get(spec.item_id, [])
        if not found:
            readings[spec.item_id] = DocumentReading(spec.item_id, filing.end, None, NOT_FOUND)
            continue
        primary = primary_statement(spec)
        ranked = sorted(
            found, key=lambda pair: (pair[0].statement != primary, -pair[1], pair[0].position)
        )
        row, probability = ranked[0]
        alternatives = tuple(r.row_id for r, _ in ranked[1:])
        value = value_for_period(row, filing.end, spec.sign)
        if value is None:
            readings[spec.item_id] = DocumentReading(
                spec.item_id, filing.end, None, NO_PERIOD_COLUMN, row.row_id, row.statement,
                probability, alternatives=alternatives,
            )
            continue
        readings[spec.item_id] = DocumentReading(
            item=spec.item_id,
            period_end=filing.end,
            value=value.value,
            reason=FOUND if value.value is not None else value.reason or NOT_FOUND,
            row_id=row.row_id,
            statement=row.statement,
            probability=probability,
            scale=value.scale,
            sign_flipped=value.sign_flipped,
            alternatives=alternatives,
        )
    return readings
