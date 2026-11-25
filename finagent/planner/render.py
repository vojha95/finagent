"""Render an investigation's answer (blueprint 8.15; Phase 6.4).

One line per requested goal. A verified value shows its fiscal year (the issuer's own), the
value and unit, the classification, its flags, the basis and the 10-K it cites (accession,
filing date, URL); a restated year shows the original value and filing alongside (#117); a
ratio that is not meaningful shows "n.m." with its reason (#120 b). A goal without a value
shows its status and reason. When no scenario was named, the answer says that Base was used
(#120 a). A comparison renders each issuer's own fiscal year and, for matching metrics, the
difference: a CALCULATION, or a CALCULATION_FROM_MODEL_ASSUMPTION when either side is a model
output, citing both filings. A verified goal with a measure withheld by design (a gap code
ending _WITHHELD, e.g. the comprehensive DSCR, 8.12) shows it after the values, with reasons.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finagent.model.ratios import NM_TEXT
from finagent.records import AnswerObservation, Goal

MODEL_OUTPUT = "CALCULATION_FROM_MODEL_ASSUMPTION"
PREFIX = {"SKIPPED": "Condition not met", "GAP": "Evidence gap", "EVIDENCE_GAP": "Evidence gap",
          "INVALID": "Rejected", "CONFLICT": "Conflict"}
QUIET_FLAGS = ("TAG_ONLY",)  # implied by SINGLE_CHANNEL
WITHHELD = "_WITHHELD"  # a gap code for a measure withheld by design, shown with the answer


@dataclass(frozen=True)
class Comparison:
    """The difference of one metric between two issuers' verified answers."""

    metric: str
    kind: str
    left: AnswerObservation
    right: AnswerObservation
    value: Decimal | float
    unit: str
    classification: str


def label(metric: str) -> str:
    return metric.replace("_", " ")


def usd(value: Decimal | float) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if abs(amount) < 10 ** 6:
        return f"USD {amount:,}"
    return f"USD {amount / 10 ** 6:,.1f}m"


def format_value(o: AnswerObservation) -> str:
    if o.value is None:
        return f"{NM_TEXT} ({o.detail})"
    if o.unit == "USD":
        return usd(o.value)
    if o.unit == "x":
        return f"{float(o.value):.2f}x"
    if o.unit == "percent":
        return f"{float(o.value) * 100:.1f}%"
    if o.unit == "days":
        return f"{float(o.value):.1f} days"
    return f"{o.value} {o.unit}".strip()


def observation_line(o: AnswerObservation, defaulted: bool = False) -> str:
    scenario = f" [{o.scenario}{', no scenario named' if defaulted else ''}]" if o.scenario \
        else ""
    flags = [f for f in o.flags if f not in QUIET_FLAGS]
    parts = [o.classification.replace("_", " ").lower()] + (["flags " + ", ".join(flags)]
                                                             if flags else [])
    parts.append(f"basis: {o.basis}")
    if o.original_value is not None and o.original_accession:
        parts.append(f"original {usd(o.original_value)} in {o.original_accession}")
    parts.append(f"10-K {o.accession}, filed {o.filed}, {o.filing_url}")
    return f"{o.issuer} FY{o.fiscal_year} {label(o.metric)}{scenario}: {format_value(o)} " \
           f"({'; '.join(parts)})."


def goal_line(goal: Goal, status: str, reason: str, detail: str) -> str:
    period = f"FY{goal.period}" if isinstance(goal.period, int) else goal.period
    what = label(goal.metric) if goal.metric else goal.kind
    if goal.kind == "explain":
        what = f"explain {what}"
    if goal.kind in ("forecast", "liquidity", "debt"):
        what += f" [{goal.scenario}, target {goal.target or 'next year'}]"
    text = f"{PREFIX.get(status, status)} | {goal.issuer} {period} {what}: {status} {reason}"
    return text + (f" ({detail})" if detail else "") + "."


def withheld_line(goal: Goal, metric: str, reason: str) -> str:
    """A measure withheld by design next to a verified answer (8.12 comprehensive DSCR)."""
    code, _, why = reason.partition(":")
    return f"Withheld | {goal.issuer} {label(metric)} [{goal.scenario}]: EVIDENCE_GAP {code} " \
           f"({why.strip()})."


def comparable(a: AnswerObservation, b: AnswerObservation) -> bool:
    return a.issuer != b.issuer and a.metric == b.metric and a.unit == b.unit \
        and a.scenario == b.scenario and a.value is not None and b.value is not None


def compare(answers: Sequence[tuple[Goal, AnswerObservation]]) -> list[Comparison]:
    """Differences for matching metrics across issuers (first issuer minus second)."""
    found = []
    for i, (ga, a) in enumerate(answers):
        for gb, b in answers[i + 1:]:
            if ga.kind != gb.kind or not comparable(a, b):
                continue
            if isinstance(a.value, Decimal) and isinstance(b.value, Decimal):
                value: Decimal | float = a.value - b.value
            else:
                value = float(a.value) - float(b.value)  # type: ignore[arg-type]
            model = MODEL_OUTPUT in (a.classification, b.classification)
            found.append(Comparison(a.metric, ga.kind, a, b, value, a.unit,
                                    MODEL_OUTPUT if model else "CALCULATION"))
    return found


def comparison_line(c: Comparison) -> str:
    shown = usd(c.value) if c.unit == "USD" else format_value(
        AnswerObservation(c.left.issuer, c.left.cik, c.left.fiscal_year, c.metric, c.unit,
                          c.value, "CALCULATION", "", "", c.left.filed, None, "-", True))
    return (f"Comparison | {c.left.issuer} FY{c.left.fiscal_year} minus {c.right.issuer} "
            f"FY{c.right.fiscal_year} {label(c.metric)}: {shown} "
            f"({c.classification.replace('_', ' ').lower()}; 10-Ks {c.left.accession} and "
            f"{c.right.accession}).")


def render(goals: Sequence[Any], comparisons: Sequence[Comparison], defaulted: bool,
           notes: Sequence[str] = ()) -> str:
    """The answer text for the requested goals (GoalResults of finagent.planner.investigate)."""
    lines: list[str] = []
    for g in goals:
        if g.status == "VERIFIED" and g.observations:
            lines.extend(observation_line(o, defaulted) for o in g.observations)
            lines.extend(withheld_line(g.goal, metric, reason)
                         for metric, reason in getattr(g, "gaps", ())
                         if reason.split(":")[0].endswith(WITHHELD))
        else:
            lines.append(goal_line(g.goal, g.status, g.reason, g.detail))
    if len({g.goal.issuer for g in goals}) > 1:
        lines.append("Fiscal years are issuer-specific; model results use each issuer's own "
                     "origin filing.")
        lines.extend(comparison_line(c) for c in comparisons)
    if defaulted:
        lines.append("No scenario was named: the Base scenario is used.")
    lines.extend(notes)
    return "\n".join(lines)
