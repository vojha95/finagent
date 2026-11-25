"""The observation gate (blueprint 8.16; reviewer decisions #117, #118; Phase 6.4).

Every observation a tool returns is checked again, independently of the tool, against the goal
and the issuer's filings as of the run date (read from the evidence store). Each rule is a small
function that returns a rejection code, or None when the observation passes:

- the metric is in the goal kind's allowed set and is the one requested;
- issuer and CIK match the goal and the filings;
- fiscal year and period end match the goal (forecast and liquidity: the target year, no period
  end); the unit and classification are those of the metric (planner.yaml answers, #118);
- the cited filing is one of the issuer's 10-Ks filed by as_of, with its own URL and filing
  date; a projection, ratio or residual cites the fiscal year's (origin's) 10-K; a reported
  fact cites its own 10-K or, for a restated year, a later one flagged RESTATED or
  FROM_LATER_FILING (#117);
- the filing date is on or before as_of; the value is finite (None only for a ratio flagged
  NOT_MEANINGFUL with its reason, #120 b); a basis is recorded;
- V2: a single-channel fact carries SINGLE_CHANNEL; a calculation or model output is validated;
  every quotation comes from a chunk of the observation's own accession and passes the quote
  check (#157, finagent.retrieval.pinned): the chunk rebuilt from the pinned document has the
  cited sha256, is text in an allowed section and contains the quote verbatim; a quote with
  no check available is rejected.

The goal is then answered by exactly one passing observation per requested metric. An outcome
that is not VERIFIED passes through unchanged (a gap, a missing primary) unless it carries
values, which makes it INVALID.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from finagent.config import AnswerSpec
from finagent.evidence.store import FilingRecord
from finagent.records import AnswerObservation, Goal, Outcome
from finagent.tools import resolve_target

VERIFIED, GAP, INVALID = "VERIFIED", "GAP", "INVALID"
PROJECTED_KINDS = ("forecast", "liquidity", "debt")
LATER_FILING_FLAGS = ("RESTATED", "FROM_LATER_FILING")
SINGLE_CHANNEL, NOT_MEANINGFUL = "SINGLE_CHANNEL", "NOT_MEANINGFUL"


@dataclass(frozen=True)
class GateContext:
    """What the gate checks one goal's observations against (built from the store)."""

    goal: Goal
    as_of: date
    filings: tuple[FilingRecord, ...]  # the issuer's 10-Ks filed by as_of
    expected_year: int | None  # fiscal year of the answer (target year when projected)
    origin_year: int | None  # the fiscal year whose 10-K a calculation must cite
    metrics: tuple[str, ...]  # requested metrics
    required: tuple[str, ...]  # requested metrics that must be answered
    allowed: frozenset[str]  # the goal kind's metrics
    answers: AnswerSpec
    # observation, cited filing -> rejection code or None (#157); None: quotes not checkable
    quote_check: Callable[[AnswerObservation, FilingRecord], str | None] | None = None

    @property
    def projected(self) -> bool:
        return self.goal.kind in PROJECTED_KINDS

    def filing(self, accession: str) -> FilingRecord | None:
        return next((f for f in self.filings if f.accession == accession), None)

    def year_filing(self, year: int | None) -> FilingRecord | None:
        return next((f for f in self.filings if f.fiscal_year == year), None)


@dataclass(frozen=True)
class GateDecision:
    """The gate's decision on one observation: accepted, or the first rule that rejected it."""

    metric: str
    accepted: bool
    code: str = ""
    detail: str = ""


def finite(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, Decimal):
        return value.is_finite()
    return isinstance(value, (int, float)) and math.isfinite(value)


def build_context(goal: Goal, as_of: date, filings: Sequence[FilingRecord], answers: AnswerSpec,
                  allowed: Sequence[str], metrics: Sequence[str],
                  required: Sequence[str] | None = None,
                  quote_check: Callable[[AnswerObservation, FilingRecord], str | None] | None
                  = None) -> GateContext:
    """The context of one goal: years resolved from the filings, never from a tool's output."""
    shown = tuple(f for f in filings if f.filed <= as_of)
    years = [f.fiscal_year for f in shown]
    origin: int | None
    if goal.kind in PROJECTED_KINDS:
        origin = max(years) if years else None
        expected = resolve_target(origin, goal.target) if origin is not None else None
    else:
        origin = (max(years) if goal.period == "latest" else int(goal.period)) if years else None
        expected = origin
    return GateContext(goal, as_of, shown, expected, origin, tuple(metrics),
                       tuple(metrics if required is None else required), frozenset(allowed),
                       answers, quote_check)


# ---------------------------------------------------------------- rules

def metric_allowed(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.metric in ctx.allowed else "METRIC_NOT_ALLOWED"


def metric_requested(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.metric in ctx.metrics else "METRIC_NOT_REQUESTED"


def issuer_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    if o.issuer != ctx.goal.issuer:
        return "ISSUER_MISMATCH"
    ciks = {f.cik for f in ctx.filings}
    return None if ciks == {o.cik} else "CIK_MISMATCH"


def fiscal_year_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.fiscal_year == ctx.expected_year else "FISCAL_YEAR_MISMATCH"


def period_end_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    if ctx.projected:
        return None if o.fiscal_period_end is None else "PERIOD_END_MISMATCH"
    own = ctx.year_filing(ctx.expected_year)
    return None if own is not None and o.fiscal_period_end == own.end else "PERIOD_END_MISMATCH"


def unit_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.unit == ctx.answers.expected(o.metric)[0] else "UNIT_MISMATCH"


def classification_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    allowed = ctx.answers.expected(o.metric)[1]
    return None if o.classification in allowed else "CLASSIFICATION_MISMATCH"


def provenance_matches(o: AnswerObservation, ctx: GateContext) -> str | None:
    """The cited filing exists by as_of with this URL and date; it is the fiscal year's (or
    origin's) 10-K, or for a reported fact a later 10-K flagged as restated (#117)."""
    cited = ctx.filing(o.accession)
    if cited is None:
        return "UNKNOWN_FILING"
    if cited.filing_url != o.filing_url or cited.filed != o.filed:
        return "PROVENANCE_MISMATCH"
    origin = ctx.year_filing(ctx.origin_year)
    if origin is not None and cited.accession == origin.accession:
        return None
    later = ctx.origin_year is not None and cited.fiscal_year > ctx.origin_year
    if ctx.goal.kind in ("fact", "explain") and later \
            and any(f in o.flags for f in LATER_FILING_FLAGS):
        return None
    return "ORIGIN_MISMATCH"


def filed_by_as_of(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.filed <= ctx.as_of else "FILED_AFTER_AS_OF"


def value_finite(o: AnswerObservation, ctx: GateContext) -> str | None:
    if o.value is None:
        return None if NOT_MEANINGFUL in o.flags and o.detail else "NOT_FINITE"
    return None if finite(o.value) else "NOT_FINITE"


def basis_recorded(o: AnswerObservation, ctx: GateContext) -> str | None:
    return None if o.basis and o.basis.strip() else "NO_BASIS"


def channels_flagged(o: AnswerObservation, ctx: GateContext) -> str | None:
    """V2: a fact on one channel carries SINGLE_CHANNEL; anything else is validated."""
    if o.validated:
        return None
    if ctx.goal.kind in ("fact", "explain") and SINGLE_CHANNEL in o.flags and len(o.channels) == 1:
        return None
    return "UNFLAGGED_SINGLE_CHANNEL" if ctx.goal.kind in ("fact", "explain") else "NOT_VALIDATED"


def quotes_from_accession(o: AnswerObservation, ctx: GateContext) -> str | None:
    """V2: a quotation must come from a chunk of the observation's own accession."""
    return None if all(q.accession == o.accession for q in o.quotes) else \
        "QUOTE_FROM_OTHER_FILING"


def quotes_verified(o: AnswerObservation, ctx: GateContext) -> str | None:
    """#157: every quote is verbatim in its chunk, rebuilt from the pinned document."""
    if not o.quotes:
        return None
    cited = ctx.filing(o.accession)
    if ctx.quote_check is None or cited is None:
        return "QUOTE_NOT_CHECKABLE"
    return ctx.quote_check(o, cited)


RULES: tuple[Callable[[AnswerObservation, GateContext], str | None], ...] = (
    metric_allowed, metric_requested, issuer_matches, fiscal_year_matches, period_end_matches,
    unit_matches, classification_matches, provenance_matches, filed_by_as_of, value_finite,
    basis_recorded, channels_flagged, quotes_from_accession, quotes_verified,
)


def check(o: AnswerObservation, ctx: GateContext) -> GateDecision:
    """The first rule that rejects the observation, or acceptance."""
    if not isinstance(o, AnswerObservation):
        return GateDecision(getattr(o, "metric", "?"), False, "NOT_AN_ANSWER")
    for rule in RULES:
        code = rule(o, ctx)
        if code:
            return GateDecision(o.metric, False, code, rule.__name__)
    return GateDecision(o.metric, True)


def gate(outcome: Outcome, ctx: GateContext) -> tuple[Outcome, tuple[GateDecision, ...]]:
    """The goal's outcome after the gate, and the decision on each observation considered."""
    if outcome.status != VERIFIED:
        if outcome.observations:
            return Outcome(INVALID, "UNVERIFIED_WITH_VALUES", detail=outcome.reason), ()
        return outcome, ()
    gaps: Mapping[str, str] = dict(outcome.gaps)
    selected = [o for o in outcome.observations if getattr(o, "metric", None) in ctx.metrics]
    decisions = tuple(check(o, ctx) for o in selected)
    accepted: list[AnswerObservation] = []
    for metric in ctx.metrics:
        mine = [(o, d) for o, d in zip(selected, decisions) if o.metric == metric]
        if not mine:
            if metric in ctx.required and metric not in gaps:
                return Outcome(INVALID, "NO_OBSERVATION_FOR_METRIC", detail=metric), decisions
            continue
        if len(mine) > 1:
            return Outcome(INVALID, "DUPLICATE_METRIC", detail=metric), decisions
        o, d = mine[0]
        if not d.accepted:
            return Outcome(INVALID, d.code, detail=f"{metric}: {d.detail}"), decisions
        accepted.append(o)
    answered = {o.metric for o in accepted}
    unanswered = [m for m in ctx.required if m not in answered]
    if unanswered:  # the tool listed them as gaps
        reason = gaps[unanswered[0]]
        return Outcome(GAP, reason.split(":")[0], detail=f"{unanswered[0]}: {reason}"), decisions
    return Outcome(VERIFIED, outcome.reason, tuple(accepted), outcome.detail), decisions
