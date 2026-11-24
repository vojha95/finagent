"""Historical ratio inputs shared by the Ratios sheet and the credit tool (#123)."""

from dataclasses import replace

from model_support import FILINGS, make_set, value_obs
from statements_support import millions

from finagent.model.ratio_inputs import (accepted_value, equity_terms, hist_metric,
                                         historical_inputs, historical_ratios,
                                         includes_finance_leases, identity, opening_values,
                                         period_days)
from finagent.model.ratios import compute_all


def test_historical_inputs_values_absent_and_sources():
    s = make_set()
    h = historical_inputs(s, 2025)
    assert h.values["revenue"] == 1.0e9 and h.values["receivables"] == 8.0e7
    assert h.values["funded_debt"] == 2.0e8 and h.values["equity"] == 4.0e8
    assert h.values["revenue_prior"] == 9.0e8 and h.days == 365.0
    assert "short_term_investments" in h.absent  # NOT_REPORTED optional input
    assert "current_assets" not in h.values and "current_assets" not in h.absent  # a gap
    assert "ebit" not in h.values  # basis operating_income has no observation
    assert h.sources["revenue"].metric == "revenue" and h.opening == {"revenue": 9.0e8}


def test_finance_leases_already_in_funded_debt_are_absent():
    funded = value_obs(2025, "funded_debt", 220, flags=("FINANCE_LEASES_INCLUDED",))
    s = make_set({(2025, "funded_debt"): funded})
    h = historical_inputs(s, 2025)
    assert includes_finance_leases(s, 2025) and not includes_finance_leases(make_set(), 2025)
    assert "finance_lease" in h.absent and "finance_lease" not in h.values
    assert "finance_lease" not in h.sources


def test_historical_ratios_equal_compute_all_on_the_same_inputs():
    s = make_set(bases={"ebit_basis": "pretax_income"})
    h = historical_inputs(s, 2025)
    r = historical_ratios(s, 2025)
    assert r == compute_all(h.values, h.absent, h.days, h.opening)
    assert r["ebit_to_gross_interest"].value == 14.0
    assert r["funded_debt_to_equity"].value == 0.5
    assert r["debt_plus_leases_to_equity"].value == (200 + 65 + 20) / 400
    assert abs(r["revenue_growth"].value - (1000 / 900 - 1)) < 1e-12
    assert r["current_ratio"].status == "GAP"


def test_bases_identity_and_equity_terms():
    s = make_set()
    assert hist_metric(s, 2025, "ebit") == "operating_income"
    assert hist_metric(s, 2025, "revenue") == "revenue"
    assert hist_metric(replace(s, bases={}), 2025, "capex") is None
    assert identity(s, 2025, "I9") is not None and identity(s, 2024, "I9") is None
    assert equity_terms(s, 2025) == [("-equity_attributable_to_parent", -millions(400))]
    assert equity_terms(s, 2024) == []


def test_opening_values_period_days_and_accepted_value():
    s = make_set()
    assert opening_values(s, None) == {} and opening_values(s, 2024) == {"revenue": 9.0e8}
    assert period_days(s, 2025) == 365.0
    undated = replace(s, filings=tuple(replace(f, start=None) for f in FILINGS))
    assert period_days(undated, 2025) is None
    assert accepted_value(None) is None
    assert accepted_value(s.get(2025, "short_term_investments")) is None
    assert accepted_value(s.get(2025, "revenue")) == 1.0e9
