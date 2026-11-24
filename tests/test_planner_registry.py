"""Action registry, goal expansion and tool arguments (blueprint 8.15; BC25)."""

from datetime import date

from planner_support import resources

from finagent.planner.registry import (actions_for, arguments, condition_goal, expand_defaults,
                                       prerequisite, resolved_period, signature)
from finagent.records import Goal

R = resources()


def resolve(issuer, period):
    return 2025 if period == "latest" else (period if period in (2024, 2025) else None)


def test_registry_from_config_has_bc25_and_placeholders():
    fact = actions_for("fact", R.planner)
    assert [(a.name, a.trigger) for a in fact] == [
        ("reported_verified", "start"), ("reported_tag_only", "DOC_MISSING"),
        ("reported_document_only", "TAG_MISSING")]
    assert actions_for("forecast", R.planner)[0].prerequisite == "statements"
    assert actions_for("explain", R.planner)[0].prerequisite == "fact"
    assert {a.name for a in R.planner.actions} >= {"debt_service_scoped", "explain_movement",
                                                    "risk_signals"}


def test_expand_defaults_one_goal_per_default_metric():
    goals = expand_defaults((Goal("MSFT", "latest", "credit"),
                             Goal("MSFT", 2024, "fact", "revenue")), R.planner)
    assert [g.metric for g in goals] == list(R.planner.kinds["credit"].defaults) + ["revenue"]
    assert expand_defaults((Goal("MSFT", "latest", "debt", "", "Base", None, True),),
                           R.planner)[0].metric == ""


def test_prerequisite_resolves_latest_so_goals_share_it():
    a = prerequisite(Goal("MSFT", "latest", "forecast", "revenue", "Base"), "statements",
                     resolve)
    b = prerequisite(Goal("MSFT", 2025, "credit", "current_ratio"), "statements", resolve)
    assert a == b == Goal("MSFT", 2025, "statements")
    assert prerequisite(Goal("MSFT", 2024, "explain", "revenue"), "fact", resolve) == \
        Goal("MSFT", 2024, "fact", "revenue")
    assert resolved_period(Goal("MSFT", 2019, "fact", "revenue"), resolve) == 2019


def test_condition_goal_is_the_shortfall_of_the_same_scenario_and_target():
    debt = Goal("MSFT", "latest", "debt", "", "Severe", "+2", True)
    assert condition_goal(debt) == Goal("MSFT", "latest", "liquidity", "liquidity_shortfall",
                                        "Severe", "+2")


def test_arguments_and_signature_share_calls_across_metrics():
    project = actions_for("forecast", R.planner)[0]
    a = arguments(Goal("MSFT", "latest", "forecast", "revenue", "Base"), project, resolve)
    b = arguments(Goal("MSFT", "latest", "forecast", "cash", "Base"), project, resolve)
    assert a == b == {"issuer": "MSFT", "period": 2025, "scenario": "Base", "target": None}
    day = date(2026, 11, 24)
    assert signature(project, a, day) == signature(project, b, day)
    assert signature(project, a, day) != signature(project, a, date(2025, 1, 1))
    fact = actions_for("fact", R.planner)[0]
    assert arguments(Goal("MSFT", 2024, "fact", "revenue"), fact, resolve)["metric"] == "revenue"
