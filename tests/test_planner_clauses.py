"""Clause splitting, conditions and fiscal years (blueprint 8.15 steps 2 and 5)."""

from planner_support import resources

from finagent.planner.clauses import (detect_condition, distinct_years, find_years,
                                      has_request_content, mask_years, numbers_in, split_clauses,
                                      strip_lead)

V = resources().vocabulary


def _content(text):
    return has_request_content(text, [m.year for m in find_years(text)], V)


def test_year_before_a_comma_or_full_stop_but_not_inside_an_amount():
    """#223: "2023," is a year; "2025,000" and "2025.5" are amounts."""
    assert [m.year for m in find_years("revenue in 2023, and net income")] == [2023]
    assert [m.year for m in find_years("revenue in 2023.")] == [2023]
    assert find_years("2025,000 dollars") == [] and find_years("about 2025.5 units") == []


def test_request_content_and_numbers():
    """#232: a clause with no company, period, metric, scenario or request word."""
    assert not _content("thanks a lot") and not _content("please")
    assert not _content("the 3 of us are curious")
    for text in ("<issuer>", "in 2024", "headroom", "why", "under severe stress", "the latest",
                 "verify it"):
        assert _content(text), text
    assert numbers_in("the 3 of us, 1,234.5 and 2025") == ("3", "1,234.5", "2025")
    assert numbers_in("thanks") == ()


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
