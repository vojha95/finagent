"""A.2 model-input facts without a document-channel class (blueprint 8.9, Appendix A.2).

Read from the tag channel for the configured period (the fiscal year, its end, or the opening
instant: the day before the fiscal year starts), with dimensions where configured (inline only).
Approved answer 1 (2026-11-24): reported SINGLE_CHANNEL (tag), accepted and flagged, with a
display_check: the visible cell at the tag's address, normalised, equals the tag.

A.2 rule for several values: keep the facts on a located face-statement row; if more than one
value remains, CONFLICT. This replaces the prototype's one-company row-label filters.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from finagent.config import ModelInputSpec
from finagent.evidence.store import FilingRecord
from finagent.ixbrl.tagstore import TagFact
from finagent.records import Observation, Outcome, RowInstance
from finagent.statements.face_block import column_for, rows_by_cell
from finagent.statements.verify_set import INLINE_ONLY_FLAG, TAG_ONLY_FLAG, base_observation
from finagent.tags.channel import (
    MULTIPLE_VALUES_INLINE,
    MULTIPLE_VALUES_PRIMARY,
    NIL,
    NOT_FOUND,
    START_UNKNOWN,
    read_item,
)
from finagent.tags.companyfacts import Period
from finagent.tags.inline import valued_facts
from finagent.document.normalize import normalise_cell
from finagent.verify import within_precision

FACE_ROW_RESOLVED = "FACE_ROW_RESOLVED"
DIMENSIONAL = "DIMENSIONAL"

# display_check results.
MATCH = "MATCH"
DIFFER = "DIFFER"
NOT_ON_FACE = "NOT_ON_FACE"  # tagged in a table that is not a located face statement
NO_CELL = "NO_CELL"  # tagged outside any table (e.g. in a sentence)


def input_period(filing: FilingRecord, spec: ModelInputSpec) -> Period | None:
    """The period to look up; None when it needs the fiscal year's start and that is unknown."""
    if spec.period == "instant":
        return Period(None, filing.end)
    if filing.start is None:
        return None
    if spec.period == "opening_instant":
        return Period(None, filing.start - timedelta(days=1))
    return Period(filing.start, filing.end)


def matching_cells(
    facts: Iterable[TagFact], filing: FilingRecord, spec: ModelInputSpec, period: Period,
    amount: Decimal, concepts: Sequence[str] | None = None,
) -> list[TagFact]:
    """Inline facts of the entry's concepts, period and dimensions carrying `amount`."""
    dims = dict(spec.dimensions)
    found = []
    for concept in concepts or spec.concepts:
        found.extend(f for f in valued_facts(facts, concept, filing, spec.period_type, period, dims)
                     if f.amount == amount)
    return found


def display_check(
    cells: Sequence[TagFact], rows: Iterable[RowInstance], amount: Decimal,
    sign: str = "either",
) -> str:
    """MATCH when a located face-statement cell holding the fact shows the same amount once
    normalised; DIFFER when such cells exist and none does; NOT_ON_FACE / NO_CELL otherwise."""
    in_tables = [f for f in cells if f.cell_address is not None]
    if not in_tables:
        return NO_CELL
    by_cell = rows_by_cell(rows)
    shown = []
    for fact in in_tables:
        row = by_cell.get((fact.cell_address.table_index, fact.cell_address.row_index))
        column = None if row is None else column_for(row, fact.cell_address.column_index)
        if row is None or column is None:
            continue
        value = normalise_cell(row, column, sign)  # type: ignore[arg-type]
        if value.value is not None:
            shown.append(value)
    if not shown:
        return NOT_ON_FACE
    target = abs(amount) if sign == "positive" else amount
    if any(within_precision(v.value, v.scale, target) for v in shown):
        return MATCH
    return DIFFER


def on_face(cells: Sequence[TagFact], rows: Iterable[RowInstance]) -> bool:
    by_cell = rows_by_cell(rows)
    return any(f.cell_address is not None and
               (f.cell_address.table_index, f.cell_address.row_index) in by_cell for f in cells)


@dataclass(frozen=True)
class Resolution:
    amount: Decimal | None
    kept: tuple[Decimal, ...]  # candidate amounts on a located face row


def face_row_resolution(
    outcome: Outcome, facts: Sequence[TagFact], filing: FilingRecord, spec: ModelInputSpec,
    period: Period, rows: Sequence[RowInstance],
) -> Resolution:
    """A.2 rule: among several tag values, keep those on a located face-statement row."""
    kept = []
    for amount in sorted({r.value for r in outcome.observations if r.value is not None}):
        if on_face(matching_cells(facts, filing, spec, period, amount), rows):
            kept.append(amount)
    return Resolution(kept[0] if len(kept) == 1 else None, tuple(kept))


def read_model_input(
    filing: FilingRecord,
    spec: ModelInputSpec,
    companyfacts: dict[str, Any],
    facts: Sequence[TagFact],
    rows: Sequence[RowInstance],
) -> Observation:
    """One A.2 fact for the filing, as a SINGLE_CHANNEL (tag) Observation or a gap."""
    period = input_period(filing, spec)
    common: dict = dict(period_type=spec.period_type, concepts=spec.concepts, statement="NOTE")
    if period is None:
        return base_observation(filing, spec.input_id, reason=START_UNKNOWN, **common)
    common.update(fiscal_period_start=period.start, fiscal_period_end=period.end)
    dims = dict(spec.dimensions) or None
    outcome = read_item(filing, spec.line_item_spec(), companyfacts, list(facts), period, dims)
    flags: tuple[str, ...] = (TAG_ONLY_FLAG,) + ((DIMENSIONAL,) if dims else ())
    amount: Decimal | None = None
    concepts = tuple(sorted({r.concept for r in outcome.observations if r.concept}))
    if outcome.status in ("VERIFIED", "MISSING_PRIMARY"):
        amount = outcome.value
        if outcome.status == "MISSING_PRIMARY":
            flags += (INLINE_ONLY_FLAG,)
    elif outcome.status == "CONFLICT" and outcome.reason in (MULTIPLE_VALUES_PRIMARY,
                                                             MULTIPLE_VALUES_INLINE):
        resolution = face_row_resolution(outcome, facts, filing, spec, period, rows)
        values = tuple(sorted({r.value for r in outcome.observations if r.value is not None}))
        if resolution.amount is None:
            return base_observation(
                filing, spec.input_id, status="CONFLICT", reason=outcome.reason,
                candidates=values, detail=f"{outcome.detail}; on face rows: "
                f"{[str(a) for a in resolution.kept]}", **(common | {"concepts": concepts}))
        amount = resolution.amount
        concepts = tuple(sorted({r.concept for r in outcome.observations
                                 if r.value == amount and r.concept}))
        flags += (FACE_ROW_RESOLVED,)
        common["candidates"] = tuple(v for v in values if v != amount)
    if amount is None:
        status = outcome.status if outcome.status in ("CONFLICT", "INVALID") else "GAP"
        reason = {NOT_FOUND: "NEITHER", NIL: NIL}.get(outcome.reason, outcome.reason)
        return base_observation(filing, spec.input_id, status=status, reason=reason,
                                detail=outcome.detail, **common)
    cells = matching_cells(facts, filing, spec, period, amount, concepts)
    common["concepts"] = concepts
    return base_observation(
        filing, spec.input_id, value=amount, tag_value=amount, classification="REPORTED_FACT",
        status="SINGLE_CHANNEL", reason="TAG_ONLY", verification="TAG_ONLY", accepted=True,
        channels=("tag",), flags=flags, display_check=display_check(cells, rows, amount, spec.sign),
        detail=outcome.detail, **common)
