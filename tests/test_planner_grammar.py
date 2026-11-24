"""M0: the prototype's exact-match regex grammar, on shared front-end output."""

from planner_support import resources

from finagent.planner.grammar import (PROTOTYPE_PHRASES, ROUTE, clean, match_kind, parse_clause,
                                      phrases)

R = resources()
V, P = R.vocabulary, R.planner


def test_phrases_are_canonical_names_plus_prototype_phrases():
    fact = phrases("fact", V)
    assert fact["revenue"] == "revenue" and fact["capex"] == "capital_expenditure"
    canonical = {ps[0] for ps in V.metrics["fact"].values()}
    assert set(fact) == canonical | set(PROTOTYPE_PHRASES["fact"])  # no other synonyms
    assert "top line" not in phrases("forecast", V)


def test_clean_drops_issuers_years_and_leading_verbs():
    assert clean("show <issuer> revenue for <year>?") == "revenue"
    assert clean("compare <issuer> and <issuer> net income in <year>") == "net income"


def test_match_kind_for_each_pattern():
    assert match_kind("what was the revenue", V) == ("fact", ("revenue",), "")
    assert match_kind("reconcile the three statements", V) == ("statements", (), "")
    assert match_kind("forecast free cash flow", V) == ("forecast", ("free_cash_flow",), "")
    assert match_kind("liquidity under severe", V) == ("liquidity", (), "Severe")
    assert match_kind("moderate scenario liquidity", V) == ("liquidity", (), "Moderate")
    assert match_kind("credit ratios", V) == ("credit", (), "")
    assert match_kind("interest coverage", V) == ("credit", ("ebit_to_gross_interest",), "")
    assert match_kind("debt service", V) == ("debt", (), "")
    assert match_kind("why did revenue fall", V) == ("explain", ("revenue",), "")
    assert match_kind("what are the main risk signals", V) == ("risk", (), "")
    assert match_kind("how much did it sell", V) is None


def test_parse_clause_year_is_period_or_target():
    fact = parse_clause("revenue for <issuer> in <year>", [2024], False, V, P)
    assert fact.kind == "fact" and fact.period == 2024 and fact.route == ROUTE
    fc = parse_clause("forecast cash for <issuer> in <year>", [2027], False, V, P)
    assert fc.period == "latest" and fc.target == 2027
    assert parse_clause("how much did <issuer> sell", [], False, V, P) is None
