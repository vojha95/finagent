"""Phase 4b integrated forecast: drivers, missing inputs, F1-F4, INVALID/GAP, point-in-time."""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from model_support import AS_OF, i9, make_set, value_obs

from finagent.config import ConfigError, load_forecast_config, load_universe
from finagent.model.checks import run_checks
from finagent.model.forecast import (
    CASH_SHORTFALL,
    GAP,
    INVALID,
    NO_TAX_BENEFIT,
    VERIFIED,
    build_forecast,
    opening_position,
)
from finagent.model.history import PointInTimeError, lookup
from finagent.records import ProjectedYear

CONFIG = load_forecast_config()
M = 1e6


def _drivers(forecast):
    return {a.driver_id: a for a in forecast.assumptions}


def test_balanced_company_verifies_and_every_check_passes():
    f = build_forecast(make_set(), AS_OF)
    assert f.status == VERIFIED and f.reasons == () and f.origin_year == 2025
    assert [y.fiscal_year for y in f.years] == [2026, 2027, 2028]
    assert {c.check_id for c in f.checks} == {"F1", "F2", "F3", "F4"}
    assert all(c.status == "PASS" for c in f.checks)
    assert len([c for c in f.checks if c.check_id == "F1"]) == 4  # opening + 3 years
    assert all(a.classification == "MODEL_ASSUMPTION" for a in f.assumptions)
    assert all(y.classification == "CALCULATION_FROM_MODEL_ASSUMPTION" for y in f.years)


def test_opening_other_lines_are_calculations_not_plugs():
    opening = opening_position(make_set(), 2025, AS_OF)
    lines = {line: o for line, o in opening.lines.items()}
    assert lines["other_assets"].value == pytest.approx(310 * M)  # 1000 - (100+80+50+400+60)
    assert lines["other_liabilities"].value == pytest.approx(245 * M)  # 600 - (70+200+65+20)
    assert lines["other_equity"].value == pytest.approx(100 * M)  # 400 - 300
    assert all(lines[l].classification == "CALCULATION"
               for l in ("other_assets", "other_liabilities", "other_equity"))
    assert [r.metric for r in lines["other_assets"].inputs] == ["total_assets"]


def test_other_balances_are_held_constant():
    f = build_forecast(make_set(), AS_OF)
    for y in f.years:
        assert y.lines["other_assets"] == pytest.approx(310 * M)
        assert y.lines["other_liabilities"] == pytest.approx(245 * M)


def test_driver_values_and_inputs():
    d = _drivers(build_forecast(make_set(), AS_OF))
    assert d["revenue_growth"].value == pytest.approx((1000 / 800) ** (1 / 3) - 1)
    assert d["revenue_growth"].basis == "CAGR_4Y" and len(d["revenue_growth"].inputs) == 4
    assert d["cogs_share"].value == pytest.approx(0.6)
    assert d["other_cash_cost_share"].value == pytest.approx((250 - 40 - 15) / 1000)
    assert d["inventory_share"].value == pytest.approx(50 / 600)
    assert d["depreciation_rate"].value == pytest.approx(40 / 400)
    assert d["tax_rate"].value == pytest.approx(30 / 140)
    assert d["debt_interest_rate"].value == pytest.approx(10 / 190)
    assert d["dividend_payout_ratio"].value == pytest.approx(0.30)  # median(0.30, 0.30, 0.30)
    assert d["repurchases"].value == pytest.approx(25 * M)
    assert d["operating_lease_rate"].value == 0.04 and d["operating_lease_rate"].status == \
        "DEFAULT"
    assert all(i.filed <= AS_OF for a in d.values() for i in a.inputs)


def test_year_one_ebit_follows_the_drivers():
    f = build_forecast(make_set(), AS_OF)
    y1 = f.years[0].lines
    revenue = 1000 * M * (1000 / 800) ** (1 / 3)
    assert y1["revenue"] == pytest.approx(revenue)
    assert y1["depreciation"] == pytest.approx(0.1 * 400 * M)  # on opening PP&E
    assert y1["ebit"] == pytest.approx(revenue * (1 - 0.6 - 0.195) - 40 * M - 18 * M)
    assert y1["operating_lease_interest"] == pytest.approx(0.04 * 65 * M)
    assert y1["debt_principal"] == pytest.approx(20 * M)


def test_revenue_growth_is_capped_and_raw_kept():
    d = _drivers(build_forecast(make_set({(2022, "revenue"): 300}), AS_OF))
    assert d["revenue_growth"].status == "CAPPED" and d["revenue_growth"].value == 0.30
    assert d["revenue_growth"].raw_value > 0.30


def test_tax_rate_bounds_and_statutory_fallback():
    d = _drivers(build_forecast(make_set({(2025, "income_tax_expense"): 100}), AS_OF))
    assert d["tax_rate"].status == "BOUNDED" and d["tax_rate"].value == 0.35
    d = _drivers(build_forecast(make_set({(2025, "pretax_income"): -5}), AS_OF))
    assert (d["tax_rate"].value, d["tax_rate"].basis) == (0.21, "STATUTORY_FALLBACK")
    assert "STATUTORY_FALLBACK" in d["tax_rate"].flags


def test_projected_loss_books_no_tax_benefit():
    f = build_forecast(make_set({(2025, "cost_of_revenue"): 990}), AS_OF)
    assert f.status == VERIFIED and NO_TAX_BENEFIT in f.flags
    assert all(y.lines["tax"] == 0 for y in f.years if y.lines["pretax"] <= 0)


def test_interest_default_rate_when_no_basis():
    d = _drivers(build_forecast(make_set(bases={"interest_basis": None}), AS_OF))
    assert (d["debt_interest_rate"].value, d["debt_interest_rate"].status) == (0.04, "DEFAULT")


def test_never_reported_inventory_is_not_applied_and_the_line_is_absent():
    f = build_forecast(make_set(gaps={(2025, "inventory"): "NOT_REPORTED"}), AS_OF)
    assert f.status == VERIFIED
    assert _drivers(f)["inventory_share"].status == "NOT_APPLIED"
    assert _drivers(f)["inventory_share"].value is None  # never 0
    assert all("inventory" not in y.lines for y in f.years)
    assert {o.line: o.value for o in f.opening}["other_assets"] == pytest.approx(360 * M)


def test_never_reported_dividends_are_not_applied():
    f = build_forecast(make_set(gaps={(y, "dividends_paid"): "NOT_REPORTED"
                                      for y in (2023, 2024, 2025)}), AS_OF)
    assert f.status == VERIFIED and _drivers(f)["dividend_payout_ratio"].status == "NOT_APPLIED"
    assert all("dividends" not in y.lines for y in f.years)


def test_repurchases_with_one_missing_year_use_two_years_flagged():
    d = _drivers(build_forecast(make_set(gaps={(2025, "repurchases_paid"): "NEITHER"}), AS_OF))
    assert d["repurchases"].value == pytest.approx(22.5 * M)
    assert "PARTIAL_HISTORY" in d["repurchases"].flags
    f = build_forecast(make_set(gaps={(2025, "repurchases_paid"): "NEITHER",
                                      (2024, "repurchases_paid"): "NEITHER"}), AS_OF)
    assert f.status == GAP and any("repurchases" in r for r in f.reasons)


def test_payout_excludes_years_with_a_loss():
    d = _drivers(build_forecast(make_set({(2023, "net_income"): -50}), AS_OF))
    assert d["dividend_payout_ratio"].value == pytest.approx(0.30)
    assert "NEGATIVE_INCOME_YEARS_EXCLUDED" in d["dividend_payout_ratio"].flags


def test_disagree_input_is_a_gap_with_no_numbers():
    f = build_forecast(make_set(gaps={(2025, "ppe_net"): "DISAGREE"}), AS_OF)
    assert f.status == GAP and f.years == () and any("ppe_net" in r and "DISAGREE" in r
                                                     for r in f.reasons)


def test_unaccepted_driver_input_is_a_gap():
    f = build_forecast(make_set(gaps={(2025, "cost_of_revenue"): "NO_IDENTITY_CHECK"}), AS_OF)
    assert f.status == GAP and f.years == ()
    assert any(r.startswith("cogs_share") for r in f.reasons)


def test_identity_precondition():
    f = build_forecast(make_set(identities=[i9(status="FAIL")]), AS_OF)
    assert f.status == GAP and any("I9" in r for r in f.reasons)
    assert any("I10" in r for r in f.reasons)  # I10 missing too


def test_failed_check_makes_the_forecast_invalid_with_no_numbers():
    f = build_forecast(make_set({(2025, "debt_principal_y1"): 500}), AS_OF)  # > opening debt
    assert f.status == INVALID and f.years == ()
    assert any(r.startswith("F4") for r in f.reasons)
    assert any(c.check_id == "F4" and c.status == "FAIL" for c in f.checks)


def test_finance_leases_inside_funded_debt_are_not_counted_twice():
    debt = value_obs(2025, "funded_debt", 200, flags=("FINANCE_LEASES_INCLUDED",))
    opening = opening_position(make_set({(2025, "funded_debt"): debt}), 2025, AS_OF)
    assert opening.value("debt") == pytest.approx(180 * M)  # 200 - 20 finance leases
    assert opening.value("finance_lease") == pytest.approx(20 * M)
    # the 20 sits inside the 200 debt line, so the listed liabilities sum to 335, not 355
    assert opening.value("other_liabilities") == pytest.approx(265 * M)


def test_refinance_switch_keeps_debt_flat():
    config = load_forecast_config(refinance_maturities=True)
    f = build_forecast(make_set(), AS_OF, config)
    assert f.status == VERIFIED and all(y.lines["debt"] == pytest.approx(200 * M)
                                        for y in f.years)
    assert f.switches["refinance_maturities"] is True


def test_working_capital_median_switch():
    config = load_forecast_config(working_capital_basis="median_3y")
    overrides = {(y, m): v for y in (2023, 2024) for m, v in (
        ("accounts_receivable", 60), ("cost_of_revenue", 500), ("inventory", 50),
        ("accounts_payable", 70))}
    d = _drivers(build_forecast(make_set(overrides), AS_OF, config))
    assert d["receivables_share"].basis == "MEDIAN_3Y"
    assert d["receivables_share"].value == pytest.approx(60 / 850)  # median of .0706,.0667,.08
    f = build_forecast(make_set(), AS_OF, config)  # history years missing
    assert f.status == GAP and any("median_3y" in r for r in f.reasons)


def test_negative_cash_is_shown_and_flagged():
    f = build_forecast(make_set({(y, "repurchases_paid"): 900 for y in (2023, 2024, 2025)}),
                       AS_OF)
    assert f.status == VERIFIED and CASH_SHORTFALL in f.flags
    assert f.years[-1].lines["cash"] < 0


def test_checks_catch_an_injected_posting_error():
    f = build_forecast(make_set(), AS_OF)
    opening = opening_position(make_set(), 2025, AS_OF)
    broken = list(f.years)
    lines = dict(broken[0].lines, cash=broken[0].lines["cash"] + 1.0)
    broken[0] = ProjectedYear(broken[0].fiscal_year, lines)
    failed = {c.check_id for c in run_checks(opening, broken, CONFIG.tolerance)
              if c.status == "FAIL" and c.fiscal_year == 2026}
    assert {"F1", "F2"} <= failed


def test_point_in_time():
    with pytest.raises(PointInTimeError):
        build_forecast(make_set(as_of=date(2026, 12, 31)), AS_OF)
    with pytest.raises(PointInTimeError):
        build_forecast(make_set(), date(2026, 1, 15))  # FY2025 filed 2026-02-01
    late = replace(value_obs(2025, "revenue", 1000), filed=date(2027, 1, 1))
    with pytest.raises(PointInTimeError):
        lookup(make_set({(2025, "revenue"): late}), 2025, "revenue", AS_OF)


def test_forecast_config_validation(tmp_path):
    bad = tmp_path / "f.yaml"
    bad.write_text((Path(__file__).parents[1] / "configs" / "forecast.yaml").read_text()
                   .replace("cap: [-0.10, 0.30]", "cap: [0.30, -0.10]"))
    with pytest.raises(ConfigError):
        load_forecast_config(bad)
    with pytest.raises(ConfigError):
        load_forecast_config(working_capital_basis="average")


def test_no_ticker_branches_in_the_model_package():
    tickers = set(load_universe())
    source = "\n".join(p.read_text() for p in (Path(__file__).parents[1] / "finagent" / "model")
                       .glob("*.py"))
    found = {t for t in tickers if re.search(rf"['\"]{t}['\"]", source)}
    assert not found


def test_payout_gap_names_the_missing_income_years():
    f = build_forecast(make_set(gaps={(y, "net_income"): "NOT_REPORTED"
                                      for y in (2023, 2024, 2025)}), AS_OF)
    assert f.status == GAP
    assert any("dividend_payout_ratio" in r and "net_income FY2024: NOT_REPORTED" in r
               for r in f.reasons)
