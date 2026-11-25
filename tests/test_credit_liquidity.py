"""Liquidity under scenarios: both headroom measures side by side (8.12; #154)."""

from dataclasses import replace

import pytest
from model_support import AS_OF, make_set

from finagent.config import BASE_SCENARIO, load_forecast_config
from finagent.credit.liquidity import (GAP, INVALID, MODELLED_CASH_SHORTFALL,
                                       NO_MODELLED_SHORTFALL, NOT_COMPUTED, VERIFIED,
                                       liquidity_row, liquidity_table, selected_check,
                                       shortfall_label)
from finagent.model.forecast import build_forecast
from finagent.records import ProjectedYear

M = 1e6
SEVERE = replace(BASE_SCENARIO, name="Severe", securities_haircut=0.2, minimum_cash_floor=10 * M)
TOL = load_forecast_config().tolerance


def table(basis: str, overrides=None, gaps=None, scenario=SEVERE):
    s = make_set(overrides if overrides is not None else {(2025, "short_term_investments"): 30},
                 gaps)
    f = build_forecast(s, AS_OF, load_forecast_config(headroom_basis=basis), scenario)
    return f, liquidity_table(f, scenario, TOL)


@pytest.mark.parametrize("basis", ["cash_plus_haircut_securities", "cash_only"])
def test_both_measures_whatever_the_basis(basis):
    f, rows = table(basis)
    assert [r.fiscal_year for r in rows] == [2026, 2027, 2028]
    for r, y in zip(rows, f.years):
        assert r.cash_headroom == pytest.approx(y.lines["cash"] - 10 * M)
        assert r.securities_reserve == pytest.approx(24 * M)  # 30 x (1 - 0.2)
        assert r.headroom_with_reserve == pytest.approx(r.cash_headroom + 24 * M)
        assert r.cash_shortfall == max(0.0, -r.cash_headroom)
        assert (r.basis, r.status) == (basis, VERIFIED)
        assert r.scheduled_principal == y.lines["debt_principal"]
        assert r.finance_lease_interest == y.lines["finance_lease_interest"]


def test_no_investments_reserve_is_absent_not_zero():
    _, rows = table("cash_plus_haircut_securities", overrides={})
    r = rows[0]
    assert r.short_term_investments is None and r.securities_reserve is None
    assert r.headroom_with_reserve == r.cash_headroom


def test_unaccepted_investments_reserve_not_computable():
    gaps = {(2025, "short_term_investments"): "DISAGREE"}
    _, rows = table("cash_plus_haircut_securities", overrides={}, gaps=gaps)
    assert all(r.headroom_with_reserve is None and r.label_with_reserve == NOT_COMPUTED
               for r in rows)
    assert all(r.status == GAP for r in rows)  # the 4c basis has no headroom line
    _, rows = table("cash_only", overrides={}, gaps=gaps)
    assert all(r.status == VERIFIED and r.headroom_with_reserve is None for r in rows)


def test_shortfall_labels():
    assert shortfall_label(5.0) == MODELLED_CASH_SHORTFALL
    assert shortfall_label(0.0) == NO_MODELLED_SHORTFALL
    assert shortfall_label(None) == NOT_COMPUTED
    burn = {(y, "repurchases_paid"): 900 for y in (2023, 2024, 2025)}
    burn[(2025, "short_term_investments")] = 5000
    _, rows = table("cash_plus_haircut_securities", overrides=burn)
    last = rows[-1]
    assert last.label == MODELLED_CASH_SHORTFALL and last.label_with_reserve == \
        NO_MODELLED_SHORTFALL


def test_selected_check_catches_a_changed_forecast_line():
    lines = {"cash": 50.0, "liquidity": 50.0, "liquidity_headroom": 40.0,
             "liquidity_shortfall": 0.0}
    assert selected_check(lines, "cash_only", 40.0, 60.0, 0.01)[0] == VERIFIED
    assert selected_check(lines, "cash_plus_haircut_securities", 40.0, 60.0, 0.01)[0] == INVALID
    row = liquidity_row("SYNT", SEVERE, ProjectedYear(2026, dict(lines, cash=60.0)), "cash_only",
                        0.01)
    assert row.status == INVALID and "differs" in row.detail


def test_table_refuses_another_scenario_and_is_empty_without_years():
    f, _ = table("cash_only")
    with pytest.raises(ValueError):
        liquidity_table(f, BASE_SCENARIO, TOL)
    assert liquidity_table(replace(f, status="GAP", years=()), SEVERE, TOL) == []
