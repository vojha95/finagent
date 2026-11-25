"""Phase 4b integrated forecast: drivers, missing inputs, F1-F4, INVALID/GAP, point-in-time."""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from model_support import AS_OF, i9, make_set, value_obs
from statements_support import pending

from finagent.config import BASE_SCENARIO, ConfigError, load_forecast_config, load_universe
from finagent.model.checks import run_checks
from finagent.model.forecast import (
    CASH_SHORTFALL,
    GAP,
    HEADROOM_NOT_COMPUTED,
    INVALID,
    LIQUIDITY_SHORTFALL,
    NO_TAX_BENEFIT,
    VERIFIED,
    build_forecast,
    no_cogs,
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
    assert {c.check_id for c in f.checks} == {"F1", "F2", "F3", "F4", "F5"}
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
    # effective rate on average (funded debt + finance leases): (200 + 20 + 180 + 18) / 2 (#76)
    assert d["interest_rate"].value == pytest.approx(10 / 209)
    assert d["finance_lease_rate"].value == d["interest_rate"].value
    assert d["finance_lease_rate"].basis == "INTEREST_RATE"
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
    assert (d["interest_rate"].value, d["interest_rate"].status) == (0.04, "DEFAULT")
    assert "no gross interest expense" in d["interest_rate"].detail
    assert (d["finance_lease_rate"].value, d["finance_lease_rate"].status) == (0.04, "DEFAULT")


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
    failed = value_obs(2025, "cost_of_revenue", 600, status="SINGLE_CHANNEL", accepted=False,
                       channels=("tag",), reason="IDENTITY_FAILED")
    f = build_forecast(make_set({(2025, "cost_of_revenue"): failed}), AS_OF)
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


def test_nil_debt_component_is_shown_in_the_opening_basis_and_flags():
    """#152: a component taken as nil in the origin year is named in the opening debt line's
    detail and in the forecast flags (and so on every model answer)."""
    debt = value_obs(2025, "funded_debt", 200,
                     flags=("NIL_UNREAD_YEAR:commercial_paper", "OMITTED:short_term_borrowings"))
    opening = opening_position(make_set({(2025, "funded_debt"): debt}), 2025, AS_OF)
    assert opening.value("debt") == pytest.approx(200 * M)
    assert opening.lines["debt"].detail == (
        "commercial paper: not read in FY2025; treated as nil; the amount may be disclosed inside "
        "a combined line or note (#152)")
    assert opening.flags == ["NIL_UNREAD_YEAR:commercial_paper"]
    f = build_forecast(make_set({(2025, "funded_debt"): debt}), AS_OF)
    assert f.status == VERIFIED and "NIL_UNREAD_YEAR:commercial_paper" in f.flags
    plain = opening_position(make_set(), 2025, AS_OF)
    assert plain.lines["debt"].detail == "" and not plain.flags


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
    with pytest.raises(ConfigError):
        load_forecast_config(headroom_basis="cash_and_securities")


def test_headroom_basis_default_is_4c_and_cash_only_follows_8_12():  # #154
    assert CONFIG.headroom_basis == "cash_plus_haircut_securities"
    s = make_set({(2025, "short_term_investments"): 30})
    severe = replace(BASE_SCENARIO, securities_haircut=0.2, minimum_cash_floor=10 * M)
    default = build_forecast(s, AS_OF, CONFIG, severe)
    cash_only = build_forecast(s, AS_OF, load_forecast_config(headroom_basis="cash_only"), severe)
    assert default.switches["headroom_basis"] == "cash_plus_haircut_securities"
    assert cash_only.switches["headroom_basis"] == "cash_only"
    for a, b in zip(default.years, cash_only.years):
        assert a.lines["liquidity_headroom"] == pytest.approx(a.lines["cash"] + 24 * M - 10 * M)
        assert b.lines["liquidity_headroom"] == pytest.approx(b.lines["cash"] - 10 * M)
        other = set(a.lines) - {"liquidity_headroom", "liquidity_shortfall"}
        assert all(a.lines[k] == b.lines[k] for k in other)  # only the headroom differs
    assert all(c.status == "PASS" for c in default.checks + cash_only.checks)
    assert any(c.check_id == "F5" and "cash_only" in c.detail for c in cash_only.checks)


def test_cash_only_headroom_needs_no_accepted_investments():  # #154
    s = make_set(gaps={(2025, "short_term_investments"): "DISAGREE"})
    default = build_forecast(s, AS_OF)
    cash_only = build_forecast(s, AS_OF, load_forecast_config(headroom_basis="cash_only"))
    assert all(HEADROOM_NOT_COMPUTED in y.flags for y in default.years)
    assert all(y.lines["liquidity_headroom"] == pytest.approx(y.lines["cash"])
               for y in cash_only.years)  # Base floor 0


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


# ---------------------------------------------------------------- 4b follow-ups (#73-#77)

def test_interest_rate_counts_finance_leases_inside_debt_once():
    overrides = {(y, "funded_debt"): value_obs(y, "funded_debt", v,
                                                flags=("FINANCE_LEASES_INCLUDED",))
                 for y, v in ((2025, 200), (2024, 180))}
    d = _drivers(build_forecast(make_set(overrides), AS_OF))
    assert d["interest_rate"].value == pytest.approx(10 / 190)


def test_unaccepted_prior_finance_lease_gives_the_default_rate():
    d = _drivers(build_forecast(make_set(gaps={(2024, "finance_lease_liability"):
                                               "DISAGREE"}), AS_OF))
    assert d["interest_rate"].status == "DEFAULT"
    assert "finance_lease_liability FY2024: DISAGREE" in d["interest_rate"].detail


def _no_cogs_set(**kwargs):
    gaps = {(2025, "operating_expense_after_cost_of_sales"): "NOT_REPORTED"}
    if (2025, "cost_of_revenue") not in kwargs.get("overrides", {}):
        gaps[(2025, "cost_of_revenue")] = "NOT_REPORTED"
    gaps.update(kwargs.pop("gaps", {}))
    overrides = {(2025, "operating_income"): 150}
    overrides.update(kwargs.pop("overrides", {}))
    return make_set(overrides, gaps, **kwargs)


def test_no_cogs_filer_uses_one_cash_operating_cost_share():
    f = build_forecast(_no_cogs_set(), AS_OF)
    assert f.status == VERIFIED and all(c.status == "PASS" for c in f.checks)
    d = _drivers(f)
    assert "cogs_share" not in d and "other_cash_cost_share" not in d
    share = d["cash_operating_cost_share"]
    assert share.value == pytest.approx((1000 - 150 - 40 - 15) / 1000)
    assert share.basis == "NO_COGS" and "NO_COGS" in share.flags
    assert d["inventory_share"].value == pytest.approx(50 / 1000)  # on revenue
    assert d["payables_share"].value == pytest.approx(70 / 1000)
    assert d["inventory_share"].basis.startswith("NO_COGS")
    y1 = f.years[0].lines
    assert "cogs" not in y1 and y1["cash_operating_costs"] == pytest.approx(
        y1["revenue"] * share.value)
    assert y1["inventory"] == pytest.approx(y1["revenue"] * 0.05)


def test_document_only_cost_of_revenue_is_not_used():
    document_only = replace(pending("cost_of_revenue", 600_000_000), fiscal_year=2025)
    s = _no_cogs_set(overrides={(2025, "cost_of_revenue"): document_only})
    assert no_cogs(s, 2025)
    assert "cash_operating_cost_share" in _drivers(build_forecast(s, AS_OF))


def test_disagreeing_cost_of_revenue_stays_a_gap():
    s = make_set(gaps={(2025, "cost_of_revenue"): "DISAGREE"})
    assert not no_cogs(s, 2025)
    f = build_forecast(s, AS_OF)
    assert f.status == GAP and any("cogs_share" in r for r in f.reasons)


def test_short_term_investments_line_and_liquidity():
    f = build_forecast(make_set({(2025, "short_term_investments"): 30}), AS_OF)
    opening = {o.line: o.value for o in f.opening}
    assert opening["short_term_investments"] == pytest.approx(30 * M)
    assert opening["other_assets"] == pytest.approx(280 * M)  # 310 - 30
    for y in f.years:
        assert y.lines["short_term_investments"] == pytest.approx(30 * M)
        assert y.lines["liquidity"] == pytest.approx(y.lines["cash"] + 30 * M)
    assert all(c.status == "PASS" for c in f.checks)


def test_no_short_term_investments_liquidity_is_cash():
    f = build_forecast(make_set(), AS_OF)
    assert all(y.lines["liquidity"] == y.lines["cash"] for y in f.years)


def test_unaccepted_short_term_investments_stay_in_other_assets_flagged():
    f = build_forecast(make_set(gaps={(2025, "short_term_investments"): "DISAGREE"}), AS_OF)
    assert f.status == VERIFIED
    assert any(flag.startswith("SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS") for flag in f.flags)
    assert all("liquidity" not in y.lines for y in f.years)


def test_liquidity_and_cash_shortfalls_are_both_reported():
    overrides = {(y, "repurchases_paid"): 900 for y in (2023, 2024, 2025)}
    overrides[(2025, "short_term_investments")] = 5000
    f = build_forecast(make_set(overrides), AS_OF)
    assert CASH_SHORTFALL in f.flags and LIQUIDITY_SHORTFALL not in f.flags
    overrides[(2025, "short_term_investments")] = 10
    f = build_forecast(make_set(overrides), AS_OF)
    assert CASH_SHORTFALL in f.flags and LIQUIDITY_SHORTFALL in f.flags


def test_missing_debt_schedule_uses_the_current_portion_for_year_one_only():
    gaps = {(2025, f"debt_principal_y{i}"): "NOT_REPORTED" for i in (1, 2, 3)}
    f = build_forecast(make_set({(2025, "long_term_debt_current"): 25}, gaps), AS_OF)
    principal = _drivers(f)["debt_principal"]
    assert f.status == GAP and principal.status == "GAP"
    assert "CURRENT_PORTION_FALLBACK" in principal.flags
    assert [r.metric for r in principal.inputs] == ["long_term_debt_current"]
    assert "debt_principal_y2" in principal.detail and f.years == ()


def test_missing_year_one_principal_only_is_filled_from_the_current_portion():
    f = build_forecast(make_set({(2025, "long_term_debt_current"): 25},
                                {(2025, "debt_principal_y1"): "NOT_REPORTED"}), AS_OF)
    principal = _drivers(f)["debt_principal"]
    assert f.status == VERIFIED and "CURRENT_PORTION_FALLBACK" in principal.flags
    assert f.years[0].lines["debt_principal"] == pytest.approx(25 * M)
