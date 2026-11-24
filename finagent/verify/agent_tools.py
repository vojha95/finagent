"""The Correlation Agent's tools (blueprint 8.8): run_identities, candidates, swap, rescale,
flip_sign, mark_review.

Each tool takes the filing state and an as_of date, and refuses a filing filed after it (rule
6). swap, rescale and flip_sign return a new state (the edit is in memory); they never act on a
missing value, and a swap only goes to a row that shows a value for the period. No tool writes
a value that is not a displayed cell value, rescaled or sign-reversed: no plugs.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finagent.document.channel import primary_statement
from finagent.document.normalize import value_for_period
from finagent.records import AgentValue, CheckResult
from finagent.verify.workspace import FilingState


class ToolError(ValueError):
    """A tool call that is not allowed (missing value, blank cell, filing after as_of)."""


@dataclass(frozen=True)
class Candidate:
    """An alternative row for an item, with M1's probability of the item for that row."""

    row_id: str
    label: str
    statement: str
    position: int
    probability: float
    value: Decimal
    scale: int


def _check_as_of(state: FilingState, as_of: date) -> None:
    if state.filing.filed > as_of:
        raise ToolError(f"{state.filing.accession} was filed {state.filing.filed}, after {as_of}")


def _value(state: FilingState, item: str) -> Decimal:
    reading = state.reading(item)
    if reading.value is None:
        raise ToolError(f"{item}: no value to change (a missing value is never set)")
    return reading.value


def run_identities(state: FilingState, as_of: date) -> tuple[CheckResult, ...]:
    """The checks of the agent's identity set (Appendix B, configs/correlation_agent.yaml) on
    the current readings; in product mode also D:<item> for each DISAGREE value."""
    _check_as_of(state, as_of)
    return state.view.checks


def candidates(state: FilingState, item: str, as_of: date) -> list[Candidate]:
    """Other rows with the item among their top-k M1 predictions that show a value for the
    period, ranked as the document channel ranks rows (primary statement, probability,
    position)."""
    _check_as_of(state, as_of)
    spec = state.configs.concept_map[item]
    current = state.reading(item).row_id
    found = []
    for prediction in state.predictions:
        ranked = dict(prediction.candidates[:state.agent.top_k])
        row = state.rows_by_id.get(prediction.row_id)
        if item not in ranked or row is None or row.row_id == current:
            continue
        value = value_for_period(row, state.filing.end, spec.sign)
        if value is None or value.value is None:
            continue
        found.append(Candidate(row.row_id, row.label, row.statement, row.position,
                               ranked[item], value.value, value.scale))
    primary = primary_statement(spec)
    return sorted(found, key=lambda c: (c.statement != primary, -c.probability, c.position))


def swap(state: FilingState, item: str, row_id: str, as_of: date) -> FilingState:
    """Read the item from another row (its own cell for the period, no rescale or flip)."""
    _check_as_of(state, as_of)
    _value(state, item)
    row = state.rows_by_id.get(row_id)
    spec = state.configs.concept_map[item]
    value = value_for_period(row, state.filing.end, spec.sign) if row else None
    if value is None or value.value is None:
        raise ToolError(f"{item}: row {row_id} shows no value for {state.filing.end}")
    return state.with_transform(item, row_id, Decimal(1), False, "swap")


def rescale(state: FilingState, item: str, factor: Decimal, as_of: date) -> FilingState:
    """A unit correction: the reading times factor (1,000 or 1/1,000). One unit correction
    relative to the row's cell at most: the cumulative factor stays 1 or a configured factor
    (x1,000 twice is not a unit misread)."""
    _check_as_of(state, as_of)
    _value(state, item)
    if factor not in state.agent.rescale_factors:
        raise ToolError(f"rescale factor {factor} is not configured")
    row_id, current, negate = state.transform(item)
    if current * factor not in {Decimal(1), *state.agent.rescale_factors}:
        raise ToolError(f"{item}: x{factor} on a reading already at x{current} is not one "
                        "unit correction")
    return state.with_transform(item, row_id, current * factor, negate, f"rescale x{factor}")


def flip_sign(state: FilingState, item: str, as_of: date) -> FilingState:
    """A sign reversal of the reading."""
    _check_as_of(state, as_of)
    _value(state, item)
    row_id, factor, negate = state.transform(item)
    return state.with_transform(item, row_id, factor, not negate, "flip_sign")


def entered(checks: tuple[CheckResult, ...], item: str) -> list[CheckResult]:
    return [c for c in checks if item in c.inputs]


def mark_review(state: FilingState, items: list[str], as_of: date,
                detail: str = "") -> tuple[AgentValue, ...]:
    """Stop and escalate: each item REVIEW with the residuals of the checks it enters; its
    original reading is kept."""
    _check_as_of(state, as_of)
    checks = state.view.checks
    found = []
    for item in items:
        original = state.original[item]
        found.append(AgentValue(
            state.filing.issuer, state.filing.fiscal_year, item, "REVIEW", original.value,
            original.row_id, original.value, original.row_id,
            checks=tuple((c.check_id, c.status, c.residual) for c in entered(checks, item)),
            detail=detail))
    return tuple(found)
