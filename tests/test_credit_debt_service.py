"""Scoped debt-service coverage and the withheld comprehensive DSCR (8.12)."""

from dataclasses import replace

import pytest
from model_support import AS_OF, make_set

from finagent.config import load_forecast_config
from finagent.credit.debt_service import (COMPREHENSIVE_DSCR_WITHHELD, EXCLUDED_OBLIGATIONS,
                                          INVALID, NEGATIVE_LABEL, NOT_APPLIED, NOT_MEANINGFUL,
                                          VERIFIED,
                                          WITHHELD_REASONS, basis_text, debt_service_table,
                                          same, scoped_debt_service, withheld_reason)
from finagent.model.forecast import build_forecast

LINES = {"cfo": 150.0, "capex": 60.0, "cfi": -60.0, "cff": -40.0, "cash": 150.0,
         "debt_principal": 20.0, "debt_interest": 12.0, "finance_lease_principal": 4.0,
         "finance_lease_interest": 4.0, "interest": 16.0, "principal": 24.0}
OPENING_CASH = 100.0  # 100 + 150 - 60 - 40 = 150


def test_scoped_dscr_adds_interest_back_to_cfo():
    """BC27 (#168): (CFO + funded and finance-lease interest - capex) / debt service."""
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, LINES)
    assert ds.status == VERIFIED and ds.cfo_less_capex == 90.0
    assert ds.interest_added_back == 16.0 and ds.cash_available == 106.0
    assert ds.debt_service == 40.0 and ds.scoped_dscr == pytest.approx(106 / 40)
    assert ds.finance_lease_payments == 8.0 and not ds.negative


def test_absent_lines_are_left_out_never_zero():
    lines = {k: v for k, v in LINES.items() if not k.startswith("finance_lease")}
    lines.update(interest=12.0, principal=20.0)
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, lines)
    assert ds.finance_lease_principal is None and ds.finance_lease_payments is None
    assert ds.interest_added_back == 12.0 and ds.cash_available == 102.0
    assert ds.debt_service == 32.0 and ds.status == VERIFIED


def test_negative_coverage_is_shown_and_labelled_never_clamped():
    """#169: capex above CFO + interest gives a negative ratio, with its label."""
    lines = dict(LINES, capex=200.0, cfi=-200.0, cash=10.0)  # 100 + 150 - 200 - 40 = 10
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, lines)
    assert ds.status == VERIFIED and ds.scoped_dscr == pytest.approx((150 + 16 - 200) / 40)
    assert ds.negative and NEGATIVE_LABEL in ds.detail
    assert NEGATIVE_LABEL in basis_text(False, negative=True)
    assert NEGATIVE_LABEL not in basis_text(False)


def test_no_debt_and_no_finance_lease_is_not_applied():
    lines = {k: v for k, v in LINES.items()
             if not k.startswith(("debt_", "finance_lease"))}
    lines.update(interest=0.0, principal=0.0)
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, lines)
    assert (ds.status, ds.debt_service, ds.scoped_dscr) == (NOT_APPLIED, None, None)


def test_zero_debt_service_is_not_meaningful():
    lines = dict(LINES, debt_principal=0.0, debt_interest=0.0, finance_lease_principal=0.0,
                 finance_lease_interest=0.0, interest=0.0, principal=0.0)
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, lines)
    assert (ds.status, ds.scoped_dscr) == (NOT_MEANINGFUL, None) and ds.detail


def test_recomputation_mismatch_is_invalid():
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH + 1, LINES)  # cash movement off
    assert (ds.status, ds.scoped_dscr) == (INVALID, None)
    ds = scoped_debt_service("SYNT", "Base", 2026, OPENING_CASH, dict(LINES, interest=17.0))
    assert ds.status == INVALID and "debt service" in ds.detail
    assert same(1.0, 1.0 + 1e-12) and not same(1.0, 1.001)


def test_table_over_a_real_forecast_recomputes_every_year():
    f = build_forecast(make_set(), AS_OF)
    rows = debt_service_table(f)
    assert [r.fiscal_year for r in rows] == [y.fiscal_year for y in f.years]
    assert all(r.status == VERIFIED for r in rows)
    for r, y in zip(rows, f.years):
        assert r.scheduled_principal == y.lines["debt_principal"]  # gross of refinancing
        assert r.cash_available == pytest.approx(
            y.lines["cfo"] - y.lines["capex"] + y.lines["interest"])  # all interest in scope
    refinanced = build_forecast(make_set(), AS_OF, load_forecast_config(refinance_maturities=True))
    assert [r.scheduled_principal for r in debt_service_table(refinanced)] == \
        [r.scheduled_principal for r in rows]
    assert debt_service_table(replace(f, status="GAP", years=())) == []


def test_basis_states_exclusions_and_withheld_reasons():
    text = basis_text(principal_refinanced=False)
    assert all(e in text for e in EXCLUDED_OBLIGATIONS) and "gross of refinancing" in text
    assert "refinance switch on" in basis_text(True)
    reason = withheld_reason()
    assert reason.startswith(COMPREHENSIVE_DSCR_WITHHELD + ": ")
    assert all(r in reason for r in WITHHELD_REASONS) and len(WITHHELD_REASONS) == 3
