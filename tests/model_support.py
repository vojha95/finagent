"""A synthetic five-year statement set that balances, for the Phase 4b forecast tests."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from statements_support import FILING, millions, obs

from finagent.records import IdentityResult
from finagent.statements.build import StatementSet
from finagent.statements.verify_set import NOT_REPORTED

AS_OF = date(2026, 11, 24)
YEARS = (2021, 2022, 2023, 2024, 2025)
FILINGS = tuple(replace(FILING, fiscal_year=y, start=date(y, 1, 1), end=date(y, 12, 31),
                        filed=date(y + 1, 2, 1), accession=f"0000999999-{y % 100 + 1:02d}-000001")
                for y in YEARS)

# Origin-year (FY2025) amounts in millions. Assets 1000 = liabilities 600 + equity 400.
ORIGIN = {
    "revenue": 1000, "cost_of_revenue": 600, "operating_expense_after_cost_of_sales": 250,
    "depreciation": 40, "operating_lease_cost": 15, "income_tax_expense": 30,
    "pretax_income": 140, "interest_expense": 10, "net_income": 110,
    "cash_and_cash_equivalents": 100, "accounts_receivable": 80, "inventory": 50,
    "ppe_net": 400, "operating_lease_rou_asset": 60, "total_assets": 1000,
    "accounts_payable": 70, "funded_debt": 200, "operating_lease_liability": 65,
    "finance_lease_liability": 20, "total_liabilities": 600,
    "equity_attributable_to_parent": 400, "retained_earnings": 300,
    "capital_expenditure": 60, "dividends_paid": 33, "repurchases_paid": 30,
    "debt_principal_y1": 20, "debt_principal_y2": 20, "debt_principal_y3": 20,
    "operating_lease_payments_y1": 18, "operating_lease_payments_y2": 17,
    "operating_lease_payments_y3": 16,
    "finance_lease_payments_y1": 5, "finance_lease_payments_y2": 5,
    "finance_lease_payments_y3": 5,
}
HISTORY = {  # earlier years used by drivers
    (2022, "revenue"): 800, (2023, "revenue"): 850, (2024, "revenue"): 900,
    (2021, "revenue"): 750, (2024, "funded_debt"): 180, (2024, "finance_lease_liability"): 18,
    (2023, "net_income"): 100, (2024, "net_income"): 110,
    (2023, "dividends_paid"): 30, (2024, "dividends_paid"): 33,
    (2023, "repurchases_paid"): 20, (2024, "repurchases_paid"): 25,
}
DERIVED = {"operating_expense_after_cost_of_sales", "funded_debt"}


def value_obs(year: int, metric: str, amount, **fields):
    filing = FILINGS[YEARS.index(year)]
    classification = "CALCULATION" if metric in DERIVED else "REPORTED_FACT"
    o = obs(metric, millions(amount), classification=fields.pop("classification",
                                                                classification), **fields)
    return replace(o, fiscal_year=year, accession=filing.accession, filed=filing.filed)


def gap_obs(year: int, metric: str, reason: str = NOT_REPORTED, status: str = "GAP"):
    filing = FILINGS[YEARS.index(year)]
    o = obs(metric, None, status=status, accepted=False, reason=reason,
            channels=("document", "tag") if status == "DISAGREE" else ())
    return replace(o, fiscal_year=year, accession=filing.accession, filed=filing.filed)


def i9(year=2025, equity_terms=(("-equity_attributable_to_parent", 400),), status="PASS"):
    operands = (("+total_assets", millions(ORIGIN["total_assets"])),
                ("-total_liabilities", -millions(ORIGIN["total_liabilities"]))) + tuple(
        (term, -millions(v)) for term, v in equity_terms)
    return IdentityResult("SYNT", year, "I9", Decimal(0), Decimal(0), status, operands=operands)


def make_set(overrides=None, gaps=None, identities=None, bases=None,
             as_of: date = AS_OF) -> StatementSet:
    """overrides: {(year, metric): amount or Observation}; gaps: {(year, metric): reason}."""
    observations = {(2025, m): value_obs(2025, m, v) for m, v in ORIGIN.items()}
    observations.update({k: value_obs(k[0], k[1], v) for k, v in HISTORY.items()})
    # No short-term investments unless a test adds them (liquidity = cash).
    observations[(2025, "short_term_investments")] = gap_obs(2025, "short_term_investments")
    for key, value in (overrides or {}).items():
        observations[key] = value if not isinstance(value, (int, float)) \
            else value_obs(key[0], key[1], value)
    for key, reason in (gaps or {}).items():
        status = "DISAGREE" if reason == "DISAGREE" else "GAP"
        observations[key] = gap_obs(key[0], key[1], "VALUES_DIFFER" if status == "DISAGREE"
                                    else reason, status)
    identities = identities if identities is not None else [
        i9(), IdentityResult("SYNT", 2025, "I10", Decimal(0), Decimal(0), "PASS")]
    year_bases = {"pretax_basis": "pretax_income", "interest_basis": "interest_expense",
                  "ebit_basis": "operating_income"}
    year_bases.update(bases or {})
    return StatementSet("SYNT", FILING.cik, as_of, FILINGS, observations, identities,
                        {y: dict(year_bases) for y in YEARS}, "test")
