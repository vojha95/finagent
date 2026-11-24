"""Coverage of forecast inputs: present, DISAGREE and NOT_REPORTED counted apart."""

from dataclasses import replace
from datetime import date

from statements_support import FILING, obs

from finagent.config import ForecastNeed
from finagent.statements.build import StatementSet
from finagent.statements.coverage import (
    DISAGREE,
    INSUFFICIENT_HISTORY,
    PRESENT,
    slots,
    summarise,
)
from finagent.statements.verify_set import NOT_REPORTED

EARLIER = replace(FILING, fiscal_year=2024)


def _set():
    observations = {
        (2025, "revenue"): obs("revenue", 10), (2024, "revenue"): obs("revenue", 9),
        (2025, "income_tax_expense"): obs("income_tax_expense", status="DISAGREE",
                                          accepted=False, reason="VALUES_DIFFER"),
        (2025, "dividends_paid"): obs("dividends_paid", reason=NOT_REPORTED),
        (2025, "pretax_income_before_equity_method"): obs("pretax_income_before_equity_method",
                                                          5),
    }
    return StatementSet("SYNT", FILING.cik, date(2026, 11, 24), (EARLIER, FILING), observations,
                        [], {2025: {"pretax_basis": "pretax_income_before_equity_method"},
                             2024: {"pretax_basis": None}}, "test")


NEEDS = [ForecastNeed("revenue", "required", 3, ""),
         ForecastNeed("income_tax_expense", "required", 1, ""),
         ForecastNeed("dividends_paid", "required", 1, ""),
         ForecastNeed("pretax_basis", "required", 1, ""),
         ForecastNeed("interest_expense", "optional", 1, "")]


def test_slots_outcomes():
    found = {(s.need_id, s.fiscal_year): s for s in slots(_set(), NEEDS)}
    assert found[("revenue", 2025)].outcome == PRESENT
    assert found[("revenue", 2024)].outcome == PRESENT
    assert found[("revenue", 2023)].outcome == INSUFFICIENT_HISTORY
    assert found[("income_tax_expense", 2025)].outcome == DISAGREE
    assert found[("dividends_paid", 2025)].outcome == NOT_REPORTED
    basis = found[("pretax_basis", 2025)]
    assert (basis.metric, basis.outcome) == ("pretax_income_before_equity_method", PRESENT)
    assert found[("interest_expense", 2025)].outcome == "NEITHER"


def test_summarise_counts_disagree_and_not_reported_apart():
    summary = summarise(slots(_set(), NEEDS), "required")
    assert (summary.slots, summary.present, summary.disagree, summary.not_reported) == (6, 3, 1, 1)
    assert summary.other_gaps == {INSUFFICIENT_HISTORY: 1}
    assert summary.share_present == 0.5 and summary.share_present_where_reported == 0.6
    assert summarise(slots(_set(), NEEDS), "optional").slots == 1
