"""Slot filler: metrics, periods, scenarios, targets and the clarification codes."""

from planner_support import resources

from finagent.planner.slots import (Slots, fill_slots, foreign_metrics, phrase_table,
                                    read_metrics, read_scenario, read_target)

R = resources()
V, P = R.vocabulary, R.planner


def _fill(body, kind, years=()):
    return fill_slots(body, kind, list(years), V, P)


def test_phrase_table_per_kind():
    assert phrase_table(V, "fact")["revenue"] == "revenue"
    assert phrase_table(V, "forecast")["cash"] == "cash"
    assert phrase_table(V, "fact")["cash"] == "cash_and_cash_equivalents"
    assert phrase_table(V, "statements") == {}


def test_read_metrics_in_clause_order():
    metrics, rest = read_metrics("net income and revenue for <issuer>", "fact", V)
    assert metrics == ("net_income", "revenue") and "revenue" not in rest


def test_foreign_metrics():
    assert foreign_metrics("and current ratio", "fact", V) == ["current ratio"]
    assert foreign_metrics("and revenue", "forecast", V) == []  # a forecast item too


def test_read_scenario():
    assert read_scenario("under severe stress", V) == ("Severe", False)
    assert read_scenario("in a stress case", V) == ("", True)
    assert read_scenario("base versus severe", V) == ("", True)
    assert read_scenario("liquidity", V) == ("", False)


def test_read_target():
    assert read_target("cash next year", V) == ("+1", False)
    assert read_target("in two years and in three years", V) == (None, True)


def test_fact_and_target_kinds():
    assert _fill("revenue in <year>", "fact", [2024]) == Slots(("revenue",), 2024, "", None)
    assert _fill("latest revenue", "fact") == Slots(("revenue",), "latest", "", None)
    assert _fill("forecast cash under severe in <year>", "forecast", [2027]) == \
        Slots(("cash",), "latest", "Severe", 2027)
    assert _fill("forecast cash in two years", "forecast") == Slots(("cash",), "latest", "",
                                                                     "+2")
    assert _fill("forecast cash next year", "forecast").target is None


def test_clarification_codes():
    assert _fill("what was it in <year>", "fact", [2024]) == "MISSING_METRIC"
    assert _fill("revenue and current ratio", "fact") == "MIXED_REQUEST"
    assert _fill("revenue in <year> and <year>", "fact", [2023, 2024]) == "MULTIPLE_PERIODS"
    assert _fill("revenue over the last three years", "fact") == "MULTIPLE_PERIODS"
    assert _fill("revenue in the prior year", "fact") == "RELATIVE_PERIOD"
    assert _fill("current ratio under the severe scenario", "credit") == \
        "SCENARIO_NOT_APPLICABLE"
    assert _fill("forecast cash in a downturn", "forecast") == "AMBIGUOUS_SCENARIO"
    assert _fill("revenue next year", "fact") == "TARGET_NOT_APPLICABLE"
    assert _fill("forecast cash next year in <year>", "forecast", [2027]) == "MULTIPLE_PERIODS"


def test_ambiguous_scenario_word_is_ordinary_on_a_kind_without_scenarios():
    assert _fill("risk signals under stress", "risk") == Slots((), "latest", "", None)
