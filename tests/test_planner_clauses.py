"""Clause splitting, conditions and fiscal years (blueprint 8.15 steps 2 and 5)."""

from planner_support import resources

from finagent.planner.clauses import (detect_condition, distinct_years, find_years, mask_years,
                                      split_clauses, strip_lead)

V = resources().vocabulary


def test_split_on_semicolon_sentence_if_and_action_word():
    assert split_clauses("assess severe liquidity for <issuer>; if there is a shortfall, check "
                         "debt service", V) == ["assess severe liquidity for <issuer>",
                                                "if there is a shortfall, check debt service"]
    assert split_clauses("show revenue. then forecast cash", V) == ["show revenue",
                                                                    "forecast cash"]
    assert split_clauses("show revenue and forecast cash", V) == ["show revenue",
                                                                  "forecast cash"]
    assert split_clauses("revenue and net income for <issuer>", V) == \
        ["revenue and net income for <issuer>"]


def test_strip_lead():
    assert strip_lead("and then please show revenue.", V) == "show revenue"


def test_condition_on_a_shortfall():
    c = detect_condition("if there is a shortfall, check debt service", V)
    assert c.conditional and c.supported and c.body == "check debt service"


def test_back_reference_is_a_supported_condition():
    c = detect_condition("if so, check debt service", V)
    assert c.conditional and c.supported and c.body == "check debt service"


def test_other_condition_at_the_start_is_unsupported():
    c = detect_condition("if revenue falls, check debt service", V)
    assert c.conditional and not c.supported


def test_mid_clause_if_without_a_shortfall_is_not_a_condition():
    c = detect_condition("check if the statements reconcile", V)
    assert not c.conditional and c.body == "check if the statements reconcile"


def test_years_forms_and_amounts():
    assert distinct_years(find_years("revenue in fy25")) == [2025]
    assert distinct_years(find_years("fiscal year 2024 revenue")) == [2024]
    assert distinct_years(find_years("revenue 2023-2025")) == [2023, 2025]
    assert distinct_years(find_years("fy23 to fy24")) == [2023, 2024]
    assert find_years("revenue above $2025 million or 2,025 or 2025 million") == []
    assert find_years("note 12-15") == []


def test_mask_years():
    text = "revenue in fy2025 vs 2024"
    assert mask_years(text, find_years(text)) == "revenue in <year> vs <year>"
