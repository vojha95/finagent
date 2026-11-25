"""The credit-NNNN run (finagent.credit.report) on a synthetic company."""

from dataclasses import replace

import pytest
from model_support import AS_OF, make_set

from finagent.config import CASH_ONLY, CASH_PLUS_SECURITIES, load_forecast_config, load_scenarios
from finagent.credit.liquidity import liquidity_table
from finagent.credit.report import (CompanyCredit, basis_consistency, company_credit,
                                    condition_runs, d1_section, headline, render_report,
                                    shortfall_years, write_outputs)
from finagent.model.forecast import build_forecast

SCENARIOS = load_scenarios()
NAMES = [s.name for s in SCENARIOS]
BURN = {(y, "repurchases_paid"): 300 for y in (2023, 2024, 2025)}
HEADER = {"run_id": "credit-test", "git_commit": "abc", "git_dirty": False,
          "evidence_manifest_sha256": "0" * 64, "as_of": AS_OF.isoformat()}


@pytest.fixture(scope="module")
def burning():
    """Cash runs out in every scenario; securities of 2,000m cover it on the 4c basis (even
    after the Severe 50% haircut)."""
    overrides = dict(BURN)
    overrides[(2025, "short_term_investments")] = 2000
    return company_credit("SYNT", "validation", make_set(overrides), AS_OF, SCENARIOS)


def test_company_credit_measures_and_both_bases(burning):
    c = burning
    assert c.origin_year == 2025 and set(c.status) == set(NAMES)
    assert all(len(c.liquidity[n]) == 3 and len(c.debt_service[n]) == 3 for n in NAMES)
    assert c.inconsistencies == []
    assert c.shortfalls[CASH_ONLY]["Base"]  # cash alone runs short
    assert c.shortfalls[CASH_PLUS_SECURITIES]["Base"] == []  # the reserve covers it
    assert "operating_working_capital_change" in c.ratios[2025]


def test_condition_runs_per_basis(burning):
    cash = condition_runs(burning, CASH_ONLY, "Base", 3)
    assert any(t.endswith("runs") for t in cash)
    assert condition_runs(burning, CASH_PLUS_SECURITIES, "Base", 3) == \
        ["skipped for every target"]
    gap = replace(burning, status={"Base": "GAP"})
    assert condition_runs(gap, CASH_ONLY, "Base", 3) == ["unknown (forecast not VERIFIED)"]
    unknown = replace(burning, not_computed={CASH_ONLY: {"Base": [2026]}},
                      shortfalls={CASH_ONLY: {"Base": []}})
    assert condition_runs(unknown, CASH_ONLY, "Base", 1) == ["+1 unknown"]


def test_shortfall_years_and_consistency():
    s = make_set(BURN)
    f = build_forecast(s, AS_OF, load_forecast_config(headroom_basis=CASH_ONLY), SCENARIOS[0])
    found, missing = shortfall_years(f)
    assert missing == [] and found == [y.fiscal_year for y in f.years
                                       if y.lines["cash"] < SCENARIOS[0].minimum_cash_floor]
    rows = liquidity_table(f, SCENARIOS[0], 0.01)
    assert basis_consistency(rows, f, CASH_ONLY, 0.01) == []
    shifted = replace(f, years=tuple(replace(y, lines=dict(y.lines, liquidity_shortfall=1e9))
                                     for y in f.years))
    assert len(basis_consistency(rows, shifted, CASH_ONLY, 0.01)) == 3


def test_report_tables_and_files(burning, tmp_path):
    table = "\n".join(headline([burning], NAMES))
    assert "| SYNT | Base |" in table and "Shortfall years, cash only" in table
    d1 = "\n".join(d1_section([burning], NAMES, 3))
    assert "Rows where the basis changes the outcome: 3 of 3" in d1
    text = render_report([burning, CompanyCredit("BAD", "validation", error="ValueError: x")],
                         HEADER, SCENARIOS, "UNCHANGED")
    assert "COMPREHENSIVE" not in text and "withheld" in text
    assert "| BAD | ERROR | ValueError: x |" in text and "UNCHANGED" in text
    write_outputs(tmp_path, [burning], HEADER)
    for name in ("credit.json", "liquidity.csv", "debt_service.csv", "ratios.csv"):
        assert (tmp_path / name).stat().st_size > 0
