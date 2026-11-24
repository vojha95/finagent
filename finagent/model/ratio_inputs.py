"""Historical ratio inputs from a statement set (blueprint 8.12, Appendix C.1; #123).

Shared by the workbook's Ratios sheet (finagent.workbook.ratios_sheet) and the planner's credit
tool (finagent.tools), so both compute a historical ratio from the same inputs:

- each ratio input key maps to a statement-set metric (HIST_KEYS; "_basis" keys resolve through
  the year's bases, e.g. EBIT = operating income or its proxy);
- an input has a value only when its observation is accepted (finagent.statements.derive
  usable); an OPTIONAL input the filer does not report (no observation, or NOT_REPORTED) is
  absent (left out of a sum, never 0); finance leases are absent when funded debt already
  includes them (FINANCE_LEASES_INCLUDED);
- funded debt is the set's funded_debt; equity is minus the sum of I9's equity operands
  (total equity, or parent equity plus NCI, plus temporary equity on the face);
- opening balances (receivables, inventory, payables) and prior revenue come from the
  previous fiscal year of the set; period days are end - start + 1 of the filing's period.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from finagent.model.ratios import RatioValue, compute_all
from finagent.records import IdentityResult, Observation
from finagent.statements.build import StatementSet
from finagent.statements.derive import usable
from finagent.statements.verify_set import NOT_REPORTED

HIST_KEYS = {  # ratio input -> reported metric (bases resolved per year)
    "current_assets": "total_current_assets", "current_liabilities": "total_current_liabilities",
    "cash": "cash_and_cash_equivalents", "short_term_investments": "short_term_investments",
    "receivables": "accounts_receivable", "inventory": "inventory",
    "payables": "accounts_payable", "operating_lease": "operating_lease_liability",
    "finance_lease": "finance_lease_liability", "total_liabilities": "total_liabilities",
    "total_assets": "total_assets", "cfo": "operating_cash_flow", "revenue": "revenue",
    "cogs": "cost_of_revenue", "gross_profit": "gross_profit", "net_income": "net_income",
    "ebit": "ebit_basis", "gross_interest": "interest_basis", "capex": "capex_basis",
}
OPTIONAL = {"short_term_investments", "receivables", "inventory", "payables",
            "operating_lease", "finance_lease"}
OPENING_KEYS = ("receivables", "inventory", "payables", "revenue")
FINANCE_LEASES_INCLUDED = "FINANCE_LEASES_INCLUDED"


def identity(s: StatementSet, year: int, identity_id: str) -> IdentityResult | None:
    """The company-level result of one identity for one fiscal year."""
    found = [r for r in s.identities if r.fiscal_year == year and r.identity_id == identity_id
             and r.subject is None]
    return found[0] if found else None


def equity_terms(s: StatementSet, year: int) -> list[tuple[str, Decimal]]:
    """The equity operands of I9 (every operand except total assets and total liabilities)."""
    r = identity(s, year, "I9")
    if r is None:
        return []
    return [(t, v) for t, v in r.operands if t not in ("+total_assets", "-total_liabilities")]


def includes_finance_leases(s: StatementSet, year: int) -> bool:
    """Funded debt already includes finance leases (the filer's debt concept does)."""
    o = s.get(year, "funded_debt")
    return o is not None and FINANCE_LEASES_INCLUDED in o.flags


def hist_metric(s: StatementSet, year: int, key: str) -> str | None:
    """The statement-set metric behind a ratio input key for one year (None: no basis)."""
    metric = HIST_KEYS[key]
    if metric.endswith("_basis"):
        return s.bases.get(year, {}).get(metric)
    return metric


def period_days(s: StatementSet, year: int) -> float | None:
    """Days in the filing's fiscal period (None when its start is not known, BC1)."""
    f = next(f for f in s.filings if f.fiscal_year == year)
    if f.start is None:
        return None
    return float((f.end - f.start).days + 1)


def accepted_value(o: Observation | None) -> float | None:
    """The observation's value when it may be used downstream, else None."""
    if o is not None and usable(o, False) and o.value is not None:
        return float(o.value)
    return None


@dataclass(frozen=True)
class HistoricalInputs:
    """One fiscal year's ratio inputs. sources: input key -> the observation used (funded debt
    and equity included), for provenance."""

    fiscal_year: int
    values: dict[str, float]
    absent: frozenset[str]
    opening: dict[str, float]
    days: float | None
    sources: dict[str, Observation] = field(default_factory=dict)


def _prior_year(s: StatementSet, year: int) -> int | None:
    years = s.years()
    i = years.index(year)
    return years[i - 1] if i > 0 else None


def opening_values(s: StatementSet, year: int | None) -> dict[str, float]:
    """The opening balances and prior revenue taken from fiscal year `year` (the prior year)."""
    if year is None:
        return {}
    found = {}
    for key in OPENING_KEYS:
        value = accepted_value(s.get(year, HIST_KEYS[key]))
        if value is not None:
            found[key] = value
    return found


def historical_inputs(s: StatementSet, year: int) -> HistoricalInputs:
    """Every ratio input of one fiscal year of the set (module docstring)."""
    values: dict[str, float] = {}
    absent: set[str] = set()
    sources: dict[str, Observation] = {}
    for key in HIST_KEYS:
        metric = hist_metric(s, year, key)
        if metric is None:
            continue
        o = s.get(year, metric)
        value = accepted_value(o)
        if value is not None:
            assert o is not None
            values[key], sources[key] = value, o
        elif key in OPTIONAL and (o is None or o.reason == NOT_REPORTED):
            absent.add(key)
    if includes_finance_leases(s, year):
        absent.add("finance_lease")
        values.pop("finance_lease", None)
        sources.pop("finance_lease", None)
    funded = s.get(year, "funded_debt")
    if (value := accepted_value(funded)) is not None:
        assert funded is not None
        values["funded_debt"], sources["funded_debt"] = value, funded
    terms = equity_terms(s, year)
    if terms:
        values["equity"] = -sum(float(v) for _, v in terms)
    opening = opening_values(s, _prior_year(s, year))
    if "revenue" in opening:
        values["revenue_prior"] = opening["revenue"]
    return HistoricalInputs(year, values, frozenset(absent), opening, period_days(s, year),
                            sources)


def historical_ratios(s: StatementSet, year: int) -> dict[str, RatioValue]:
    """Every ratio of finagent.model.ratios for one fiscal year of the set."""
    inputs = historical_inputs(s, year)
    return compute_all(inputs.values, inputs.absent, inputs.days, inputs.opening)
