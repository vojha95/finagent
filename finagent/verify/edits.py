"""In-memory override hooks on document readings (blueprint 8.8, 10.2; Phase 5).

A DocumentEdit replaces one item's document reading for one filing period by another reading of
the same filing: another row's value for the period (swap), the reading times 1,000 or 1/1,000
(rescale) or with its sign reversed (flip_sign). Correlation Agent corrections and E2 injections
go through the same hook, as the prototype's replays did with its evidence override callables.
The hook runs after the document channel has read the filing and never touches evidence/.

Every edited value is a displayed cell value, normalised as the document channel does, then
rescaled or sign-reversed: never a plug, and never a value for a blank cell (an edit onto a row
with no value for the period raises).
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from finagent.config import LineItemSpec
from finagent.document.channel import FOUND, DocumentReading
from finagent.document.normalize import value_for_period
from finagent.evidence.store import FilingRecord
from finagent.records import DocumentEdit, Prediction, RowInstance

EditKey = tuple[str, object, str]  # (accession, period end, item)


class EditError(ValueError):
    """An edit that would not give a displayed value (blank cell, unknown row)."""


def probability(predictions: Iterable[Prediction], row_id: str, item: str) -> float:
    """M1's probability of `item` for the row (0 when the item is not among its candidates)."""
    for p in predictions:
        if p.row_id == row_id:
            return next((q for c, q in p.candidates if c == item), 0.0)
    return 0.0


def factor_scale(factor: Decimal) -> int:
    """The power of ten a rescale factor shifts the displayed precision by (1000 -> 3)."""
    return factor.normalize().adjusted()


def edited_reading(edit: DocumentEdit, rows_by_id: Mapping[str, RowInstance],
                   spec: LineItemSpec, original: DocumentReading) -> DocumentReading:
    """The reading an edit gives: the row's value for the period x factor, negated if asked."""
    row = rows_by_id.get(edit.row_id)
    if row is None:
        raise EditError(f"{edit.item}: row {edit.row_id} is not in the filing")
    value = value_for_period(row, edit.period_end, spec.sign)
    if value is None or value.value is None:
        raise EditError(f"{edit.item}: row {edit.row_id} shows no value for {edit.period_end}")
    amount = value.value * edit.factor
    if edit.negate:
        amount = -amount
    first = original.edit is None
    return DocumentReading(
        item=edit.item, period_end=edit.period_end, value=amount, reason=FOUND,
        row_id=row.row_id, statement=row.statement, probability=edit.probability,
        scale=value.scale + factor_scale(edit.factor), sign_flipped=value.sign_flipped,
        alternatives=original.alternatives, edit=edit,
        original_value=original.value if first else original.original_value,
        original_row_id=original.row_id if first else original.original_row_id,
    )


def unedited(reading: DocumentReading) -> bool:
    return reading.edit is None


def apply_edits(
    documents: Mapping[str, DocumentReading],
    edits: Sequence[DocumentEdit],
    filing: FilingRecord,
    rows: Sequence[RowInstance],
    specs: Mapping[str, LineItemSpec],
) -> dict[str, DocumentReading]:
    """The filing's readings for its period `filing.end` with the matching edits applied.

    Edits for another accession or period (e.g. a prior-year column) are ignored. Two edits of
    one item for one period raise: an item has one reading.
    """
    mine = [e for e in edits if e.accession == filing.accession and e.period_end == filing.end]
    items = [e.item for e in mine]
    if len(set(items)) != len(items):
        raise EditError(f"{filing.accession}: two edits of one item")
    result = dict(documents)
    if not mine:
        return result
    rows_by_id = {r.row_id: r for r in rows}
    for edit in mine:
        if edit.item not in result or edit.item not in specs:
            raise EditError(f"{edit.item}: not a canonical item of this filing")
        result[edit.item] = edited_reading(edit, rows_by_id, specs[edit.item], result[edit.item])
    return result


def edit_key(edit: DocumentEdit) -> EditKey:
    return (edit.accession, edit.period_end, edit.item)


def without_source(edits: Iterable[DocumentEdit], source: str) -> list[DocumentEdit]:
    return [e for e in edits if e.source != source]
