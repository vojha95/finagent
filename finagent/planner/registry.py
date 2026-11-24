"""Action registry, goal expansion and tool arguments (blueprint 8.15; Phase 6.4).

The registry is configs/planner.yaml `actions` (BC25 adds TAG_MISSING -> reported_document_only).
Goal expansion before the loop:
- a goal whose metric is "" and whose kind has defaults (forecast, credit, liquidity) becomes
  one goal per default metric, so one missing ratio does not hide the others (the tool call is
  shared and reused);
- a goal with a prerequisite kind gets that goal for the same issuer and fiscal year
  (statements; fact for explain), with "latest" resolved to the fiscal year so goals on the
  same year share one prerequisite;
- a conditional debt goal gets a liquidity shortfall goal for its scenario and target when the
  question has none (it is the condition), and its statements prerequisite only once the
  condition is met.
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from finagent.config import PlannerConfig
from finagent.records import Action, Goal

SHORTFALL = "liquidity_shortfall"
ARGUMENTS: dict[str, tuple[str, ...]] = {  # action -> goal fields passed (besides as_of)
    "reported_verified": ("issuer", "period", "metric"),
    "reported_tag_only": ("issuer", "period", "metric"),
    "reported_document_only": ("issuer", "period", "metric"),
    "reconcile": ("issuer", "period"),
    "project": ("issuer", "period", "scenario", "target"),
    "credit_ratios": ("issuer", "period"),
    "liquidity": ("issuer", "period", "scenario", "target"),
    "debt_service_scoped": ("issuer", "period", "scenario", "target"),
    "explain_movement": ("issuer", "period", "metric"),
    "risk_signals": ("issuer", "period"),
}

Resolver = Callable[[str, Any], int | None]  # (issuer, period) -> fiscal year or None


def actions_for(kind: str, config: PlannerConfig) -> tuple[Action, ...]:
    """The registry's actions for one goal kind, lowest priority first."""
    return tuple(a for a in config.actions if a.kind == kind)


def expand_defaults(goals: Sequence[Goal], config: PlannerConfig) -> tuple[Goal, ...]:
    """Each goal with no metric becomes one goal per default metric of its kind."""
    found: list[Goal] = []
    for g in goals:
        defaults = config.kinds[g.kind].defaults
        if g.metric == "" and defaults:
            found.extend(Goal(g.issuer, g.period, g.kind, m, g.scenario, g.target,
                              g.conditional) for m in defaults)
        else:
            found.append(g)
    return tuple(dict.fromkeys(found))


def resolved_period(goal: Goal, resolve: Resolver) -> int | str:
    """The goal's fiscal year when the filings give one, else its period as compiled."""
    year = resolve(goal.issuer, goal.period)
    return goal.period if year is None else year


def prerequisite(goal: Goal, kind: str, resolve: Resolver) -> Goal:
    """The prerequisite goal of `kind` for the goal's issuer and fiscal year."""
    period = resolved_period(goal, resolve)
    metric = goal.metric if kind in ("fact", "explain") else ""
    return Goal(goal.issuer, period, kind, metric)  # type: ignore[arg-type]


def condition_goal(goal: Goal) -> Goal:
    """The liquidity shortfall goal a conditional debt goal depends on."""
    return Goal(goal.issuer, goal.period, "liquidity", SHORTFALL, goal.scenario, goal.target)


def arguments(goal: Goal, action: Action, resolve: Resolver) -> dict[str, Any]:
    """The tool arguments for one goal (period resolved to the fiscal year); as_of is added by
    the loop. Goals that differ only in fields an action does not take share one call."""
    values = {"issuer": goal.issuer, "period": resolved_period(goal, resolve),
              "metric": goal.metric, "scenario": goal.scenario, "target": goal.target}
    return {name: values[name] for name in ARGUMENTS[action.name]}


def signature(action: Action, args: dict[str, Any], as_of: date) -> tuple:
    """The identity of a tool call: identical calls are reused, not repeated."""
    return (action.name, tuple(sorted((k, str(v)) for k, v in args.items())), as_of.isoformat())
