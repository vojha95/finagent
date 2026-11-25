"""C.1 ratio definitions (finagent.model.ratios; Phase 4d)."""

import pytest

from finagent.model.ratios import BY_ID, RATIOS, balance_change, compute, compute_all

BASE = {"current_assets": 300.0, "current_liabilities": 200.0, "cash": 100.0,
        "receivables": 80.0, "inventory": 50.0, "payables": 70.0, "funded_debt": 200.0,
        "operating_lease": 65.0, "finance_lease": 20.0, "equity": 400.0,
        "total_liabilities": 600.0, "total_assets": 1000.0, "ebit": 95.0,
        "gross_interest": 10.0, "cfo": 150.0, "capex": 60.0, "revenue": 1000.0, "cogs": 600.0,
        "gross_profit": 400.0, "net_income": 110.0, "revenue_prior": 900.0}
OPENING = {"receivables": 60.0, "inventory": 40.0, "payables": 50.0}


def test_ratio_values_follow_c1():
    r = compute_all(BASE, absent={"short_term_investments"}, days=365.0, opening=OPENING)
    assert r["current_ratio"].value == pytest.approx(1.5)
    assert r["quick_ratio"].value == pytest.approx(180 / 200)  # securities absent: left out
    assert r["debt_plus_leases_to_equity"].value == pytest.approx(285 / 400)
    assert r["fcf_to_funded_debt"].value == pytest.approx(90 / 200)
    assert r["dso"].value == pytest.approx(70 * 365 / 1000)
    assert r["dio"].value == pytest.approx(45 * 365 / 600)
    assert r["cash_conversion_cycle"].value == pytest.approx(
        r["dso"].value + r["dio"].value - r["dpo"].value)
    assert r["revenue_growth"].value == pytest.approx(1000 / 900 - 1)


def test_non_positive_denominator_is_not_meaningful():
    r = compute(BY_ID["funded_debt_to_equity"], {**BASE, "equity": -5.0})
    assert (r.status, r.value) == ("NM", None)
    assert compute(BY_ID["ebit_to_gross_interest"], {**BASE, "gross_interest": 0.0}).status == "NM"


def test_missing_input_is_gap_never_zero():
    values = {k: v for k, v in BASE.items() if k != "cash"}
    assert compute(BY_ID["quick_ratio"], values, absent={"short_term_investments"}).status \
        == "GAP"
    # an optional item the filer does not report is left out; one with no value is a GAP
    no_sti = compute(BY_ID["quick_ratio"], BASE)
    assert no_sti.status == "GAP" and "short_term_investments" in no_sti.detail


def test_average_days_needs_an_opening_balance_and_period_days():
    spec = BY_ID["dso"]
    assert compute(spec, BASE, days=365.0, opening={}).status == "GAP"
    assert compute(spec, BASE, days=None, opening=OPENING).status == "GAP"
    assert compute(spec, BASE, absent={"receivables"}, days=365.0).status == "NOT_APPLIED"


def test_cash_conversion_cycle_leaves_out_inventory_when_not_reported():
    values = {k: v for k, v in BASE.items() if k != "inventory"}
    r = compute_all(values, absent={"inventory", "short_term_investments"}, days=365.0,
                    opening=OPENING)
    assert r["dio"].status == "NOT_APPLIED"
    assert r["cash_conversion_cycle"].value == pytest.approx(r["dso"].value - r["dpo"].value)


def test_every_ratio_has_a_known_kind_and_format():
    assert {r.kind for r in RATIOS} <= {"ratio", "average_days", "difference", "growth",
                                        "balance_change"}
    assert {r.fmt for r in RATIOS} <= {"ratio", "percent", "days", "dollars"}


def test_operating_working_capital_change_is_closing_minus_opening():  # #156
    r = compute_all(BASE, absent={"short_term_investments"}, days=365.0, opening=OPENING)
    assert r["operating_working_capital_change"].value == pytest.approx((80 - 60) + (50 - 40)
                                                                        - (70 - 50))


def test_working_capital_change_leaves_out_unreported_lines_never_zero():  # #156
    spec = BY_ID["operating_working_capital_change"]
    values = {k: v for k, v in BASE.items() if k != "inventory"}
    opening = {k: v for k, v in OPENING.items() if k != "inventory"}
    r = balance_change(spec, values, {"inventory"}, opening)
    assert (r.status, r.value) == ("OK", pytest.approx((80 - 60) - (70 - 50)))
    assert balance_change(spec, values, {"inventory"}, OPENING).status == "GAP"  # opening only
    assert balance_change(spec, BASE, (), {"receivables": 60.0}).status == "GAP"
    none = balance_change(spec, {}, {"receivables", "inventory", "payables"}, {})
    assert (none.status, none.value) == ("NOT_APPLIED", None)
    assert compute(spec, BASE, opening=OPENING).value == pytest.approx(10.0)
