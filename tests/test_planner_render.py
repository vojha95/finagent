"""Answer rendering (blueprint 8.15; #117, #120)."""

from dataclasses import dataclass, replace
from decimal import Decimal

from tools_support import answer, by_year, filings

from finagent.planner.render import (Comparison, compare, comparison_line, format_value,
                                     goal_line, label, observation_line, render, usd)
from finagent.records import AnswerObservation, Goal

F = by_year(filings(), 2024)
G = by_year(filings("AMZN", "0001018724"), 2024)


def test_usd_and_units():
    assert usd(Decimal("245122000000")) == "USD 245,122.0m"
    assert usd(Decimal(0)) == "USD 0" and usd(-1.5e9) == "USD -1,500.0m"
    assert format_value(answer(F, "current_ratio", 1.234, unit="x")) == "1.23x"
    assert format_value(answer(F, "gross_margin", 0.4567, unit="percent")) == "45.7%"
    assert format_value(answer(F, "dso", 45.04, unit="days")) == "45.0 days"
    nm = answer(F, "ebit_to_gross_interest", None, unit="x", detail="non-positive denominator")
    assert format_value(nm) == "n.m. (non-positive denominator)"
    assert label("free_cash_flow") == "free cash flow"
    assert format_value(answer(F, "R8.ebit_margin_change", -4.04, unit="pp")) == "-4.0 pp"
    assert format_value(answer(F, "signals_fired", Decimal(3), unit="count")) == "3"


def test_quote_lines_signal_gaps_and_explain_support():  # 7.3, D7
    from types import SimpleNamespace

    from finagent.planner.render import quote_lines, signal_gaps_line, support_lines
    from finagent.records import Quote

    q = Quote("c1", F.accession, "Receivables rose.", "ITEM_7", 0.5, "s")
    shown: set = set()
    o = answer(F, "R1.dso_change", 6.0, unit="days", quotes=(q,))
    assert quote_lines(o, shown) == ['  Quote | ITEM_7, chunk c1, score 0.50: '
                                     '"Receivables rose."']
    assert quote_lines(o, shown) == []  # once per goal
    gap = answer(F, "R5.funded_debt_to_ebitda", 3.4, unit="x", flags=("EVIDENCE_GAP",))
    assert quote_lines(gap, shown)[0].startswith("  Evidence gap | R5:")
    goal = Goal("MSFT", 2024, "risk")
    assert signal_gaps_line(goal, [("R2", "GAP: NO_COGS"), ("R3", "NOT_FIRED")]) == \
        "Other signals | MSFT: R2 GAP: NO_COGS; R3 NOT_FIRED."
    explain = Goal("MSFT", 2024, "explain", "revenue")
    fact = SimpleNamespace(goal=Goal("MSFT", 2024, "fact", "revenue"), requested=False,
                           status="VERIFIED", observations=(answer(F, "revenue", Decimal(5)),))
    assert support_lines(explain, "GAP", [fact])[0].startswith("Verified figure | MSFT FY2024")
    assert support_lines(explain, "VERIFIED", [fact]) == []
    tag_only = SimpleNamespace(goal=fact.goal, requested=False, status="VERIFIED",
                               observations=(answer(F, "revenue", Decimal(5),
                                                    flags=("SINGLE_CHANNEL",)),))
    assert support_lines(explain, "GAP", [tag_only])[0].startswith("Single-channel figure |")


def test_observation_line_shows_provenance_flags_and_restated_original():
    o = answer(F, "revenue", Decimal("1000000000"), flags=("RESTATED", "TAG_ONLY"),
               original_value=Decimal("900000000"), original_accession="0000-old")
    line = observation_line(o)
    assert line.startswith("MSFT FY2024 revenue: USD 1,000.0m (reported fact; flags RESTATED;")
    assert "original USD 900.0m in 0000-old" in line and F.accession in line
    assert F.filing_url in line and str(F.filed) in line and "TAG_ONLY" not in line
    p = answer(F, "revenue", 1.0e9, scenario="Base", projected=True,
               classification="CALCULATION_FROM_MODEL_ASSUMPTION")
    assert "[Base, no scenario named]" in observation_line(p, defaulted=True)


def test_goal_line_for_gaps_and_skips():
    assert goal_line(Goal("MSFT", 2024, "fact", "revenue"), "GAP", "NOT_REPORTED", "") == \
        "Evidence gap | MSFT FY2024 revenue: GAP NOT_REPORTED."
    line = goal_line(Goal("MSFT", "latest", "debt", "", "Severe", None, True), "SKIPPED",
                     "NO_SHORTFALL", "no shortfall")
    assert line.startswith("Condition not met | MSFT latest debt [Severe, target next year]")
    assert "explain revenue" in goal_line(Goal("MSFT", 2024, "explain", "revenue"), "GAP",
                                          "NOT_YET_AVAILABLE", "")


def test_compare_difference_and_classification():
    a = answer(F, "net_income", Decimal(300))
    b = answer(G, "net_income", Decimal(100))
    (c,) = compare([(Goal("MSFT", 2024, "fact", "net_income"), a),
                    (Goal("AMZN", 2024, "fact", "net_income"), b)])
    assert c.value == Decimal(200) and c.classification == "CALCULATION"
    assert F.accession in comparison_line(c) and G.accession in comparison_line(c)
    pa = replace(a, classification="CALCULATION_FROM_MODEL_ASSUMPTION", value=3.0)
    pb = replace(b, classification="CALCULATION_FROM_MODEL_ASSUMPTION", value=1.0)
    (m,) = compare([(Goal("MSFT", "latest", "forecast", "net_income"), pa),
                    (Goal("AMZN", "latest", "forecast", "net_income"), pb)])
    assert m.classification == "CALCULATION_FROM_MODEL_ASSUMPTION" and m.value == 2.0
    assert compare([(Goal("MSFT", 2024, "fact", "net_income"), a),
                    (Goal("AMZN", 2024, "fact", "revenue"), replace(b, metric="revenue"))]) == []


@dataclass
class _Result:
    goal: Goal
    status: str
    reason: str
    detail: str
    observations: tuple[AnswerObservation, ...]


def test_render_lists_goals_comparison_and_base_note():
    a = answer(F, "net_income", Decimal(300))
    b = answer(G, "net_income", Decimal(100))
    goals = [_Result(Goal("MSFT", 2024, "fact", "net_income"), "VERIFIED", "AGREE", "", (a,)),
             _Result(Goal("AMZN", 2024, "fact", "net_income"), "VERIFIED", "AGREE", "", (b,))]
    c = Comparison("net_income", "fact", a, b, Decimal(200), "USD", "CALCULATION")
    text = render(goals, [c], defaulted=True)
    assert text.count("\n") == 4 and "issuer-specific" in text and "Comparison |" in text
    assert text.endswith("No scenario was named: the Base scenario is used.")
