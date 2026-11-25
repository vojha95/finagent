"""Forecast drivers (blueprint 8.10): one function per driver, each a MODEL_ASSUMPTION record.

Every driver lists the accepted statement-set values it rests on (InputRef) and its basis.
Status: SET, CAPPED / BOUNDED (raw value kept), DEFAULT (a stated default rate), NOT_APPLIED
(the filer never reports the item: the line does not exist, value None, never 0) or GAP (an
input exists but has no accepted value: DISAGREE, IDENTITY_FAILED, INPUT_GAP, ...). Floats only
here (model calculations); the inputs keep their Decimal amounts.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from statistics import median

from finagent.config import ForecastConfig
from finagent.model.history import Lookup, lookup, lookup_basis
from finagent.records import InputRef, ModelAssumption
from finagent.statements.build import StatementSet
from finagent.statements.derive import NIL_UNREAD_YEAR

# Flags.
PARTIAL_HISTORY = "PARTIAL_HISTORY"  # a median over fewer years than configured
NEGATIVE_INCOME_YEARS_EXCLUDED = "NEGATIVE_INCOME_YEARS_EXCLUDED"
STATUTORY_FALLBACK = "STATUTORY_FALLBACK"
DEFAULT_RATE = "DEFAULT_RATE"
NO_COGS = "NO_COGS"  # no cost-of-sales line with a tag reading: one total cash cost share (#77a)
CURRENT_PORTION_FALLBACK = "CURRENT_PORTION_FALLBACK"  # year-1 principal from the balance sheet
FINANCE_LEASES_INCLUDED = "FINANCE_LEASES_INCLUDED"

SET, CAPPED, BOUNDED, DEFAULT, NOT_APPLIED, GAP = (
    "SET", "CAPPED", "BOUNDED", "DEFAULT", "NOT_APPLIED", "GAP")


def gap(driver_id: str, missing: Sequence[Lookup], detail: str = "") -> ModelAssumption:
    why = "; ".join(f"{m.metric} FY{m.fiscal_year}: {m.reason}" for m in missing)
    return ModelAssumption(driver_id, None, "GAP", GAP, detail=(detail or why))


def not_applied(driver_id: str, lookup_: Lookup, detail: str = "") -> ModelAssumption:
    return ModelAssumption(driver_id, None, "NOT_REPORTED", NOT_APPLIED,
                           flags=(f"NOT_APPLIED:{lookup_.metric}",),
                           detail=detail or f"{lookup_.metric} is never reported by the filer; "
                                            "the line does not exist in the projection")


def _flags(refs: Sequence[InputRef], keep: tuple[str, ...] = ("EBIT_PROXY", "SUBSTITUTE",
                                                              "FINANCE_LEASES_INCLUDED")
           ) -> tuple[str, ...]:
    found = sorted({f for r in refs for f in r.flags if f.startswith(keep)})
    return tuple(found)


def clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(max(value, bounds[0]), bounds[1])


def ratio_driver(driver_id: str, numerator: Lookup, denominator: Lookup,
                 basis: str = "LATEST", allow_not_reported: bool = False) -> ModelAssumption:
    """numerator / denominator for the latest year. A never-reported numerator makes the driver
    NOT_APPLIED when allowed (e.g. inventory); any other missing input is a GAP."""
    if allow_not_reported and numerator.not_reported:
        return not_applied(driver_id, numerator)
    missing = [x for x in (numerator, denominator) if not x.found]
    if missing:
        return gap(driver_id, missing)
    if denominator.value == 0:
        return gap(driver_id, [], f"{denominator.metric} is zero")
    refs = (numerator.ref, denominator.ref)
    return ModelAssumption(driver_id, numerator.value / denominator.value, basis, SET, refs,
                           flags=_flags(refs),
                           detail=f"{numerator.metric} / {denominator.metric}")


def revenue_growth(s: StatementSet, origin: int, config: ForecastConfig,
                   as_of: date) -> ModelAssumption:
    """CAGR over the latest `growth_years` fiscal-year revenues, capped."""
    years = list(range(origin - config.growth_years + 1, origin + 1))
    revenues = [lookup(s, y, "revenue", as_of) for y in years]
    missing = [r for r in revenues if not r.found]
    if missing:
        return gap("revenue_growth", missing)
    first, last = revenues[0].value, revenues[-1].value
    if first <= 0 or last <= 0:
        return gap("revenue_growth", [], "revenue not positive")
    raw = (last / first) ** (1 / (len(years) - 1)) - 1
    value = clamp(raw, config.growth_cap)
    return ModelAssumption("revenue_growth", value, f"CAGR_{len(years)}Y",
                           CAPPED if value != raw else SET, tuple(r.ref for r in revenues),
                           raw_value=raw, bounds=config.growth_cap,
                           detail=f"(revenue FY{years[-1]} / FY{years[0]})^(1/{len(years) - 1}) - 1")


def other_cash_cost_share(s: StatementSet, origin: int, as_of: date) -> ModelAssumption:
    """(operating expense after COGS - depreciation - operating lease cost) / revenue (8.10).

    A filer that reports no operating leases has no lease cost term (listed). Year 0 then
    reproduces EBIT: revenue - COGS - other - depreciation - lease cost = GP - opex after COGS.
    """
    opex = lookup(s, origin, "operating_expense_after_cost_of_sales", as_of)
    depreciation = lookup(s, origin, "depreciation", as_of)
    lease_cost = lookup(s, origin, "operating_lease_cost", as_of)
    revenue = lookup(s, origin, "revenue", as_of)
    required = [opex, depreciation, revenue] + ([] if lease_cost.not_reported else [lease_cost])
    missing = [x for x in required if not x.found]
    if missing:
        return gap("other_cash_cost_share", missing)
    lease = lease_cost.value if lease_cost.found else 0.0
    refs = tuple(x.ref for x in required)
    flags = _flags(refs) + (("NOT_APPLIED:operating_lease_cost",) if lease_cost.not_reported
                            else ())
    value = (opex.value - depreciation.value - lease) / revenue.value
    return ModelAssumption("other_cash_cost_share", value, "LATEST", SET, refs, flags=flags,
                           detail="(opex after COGS - depreciation - operating lease cost) / "
                                  "revenue")


def cash_operating_cost_share(s: StatementSet, origin: int, as_of: date) -> ModelAssumption:
    """NO_COGS basis (#77a): (revenue - EBIT - depreciation - operating lease cost) / revenue,
    one total cash operating cost share for a filer with no cost-of-sales line. Year 0 then
    reproduces EBIT. No operating leases reported: no lease cost term (listed)."""
    revenue = lookup(s, origin, "revenue", as_of)
    ebit = lookup_basis(s, origin, "ebit_basis", as_of)
    depreciation = lookup(s, origin, "depreciation", as_of)
    lease_cost = lookup(s, origin, "operating_lease_cost", as_of)
    required = [revenue, ebit, depreciation] + ([] if lease_cost.not_reported else [lease_cost])
    missing = [x for x in required if not x.found]
    if missing:
        return gap("cash_operating_cost_share", missing)
    if revenue.value == 0:
        return gap("cash_operating_cost_share", [], "revenue is zero")
    lease = lease_cost.value if lease_cost.found else 0.0
    refs = tuple(x.ref for x in required)
    flags = (NO_COGS,) + _flags(refs) + (("NOT_APPLIED:operating_lease_cost",)
                                         if lease_cost.not_reported else ())
    value = (revenue.value - ebit.value - depreciation.value - lease) / revenue.value
    return ModelAssumption("cash_operating_cost_share", value, NO_COGS, SET, refs, flags=flags,
                           detail=f"(revenue - {ebit.metric} - depreciation - operating lease "
                                  "cost) / revenue")


def working_capital_share(driver_id: str, s: StatementSet, origin: int, balance: str,
                          driver: str, config: ForecastConfig, as_of: date,
                          basis_flag: str | None = None) -> ModelAssumption:
    """balance / driver (revenue or COGS): latest year, or the median of the latest years.
    basis_flag (NO_COGS) is recorded as the basis and a flag."""
    found = _working_capital_share(driver_id, s, origin, balance, driver, config, as_of)
    if basis_flag is None or found.status in (NOT_APPLIED, GAP):
        return found
    return replace(found, basis=f"{basis_flag};{found.basis}", flags=(basis_flag,) + found.flags)


def _working_capital_share(driver_id: str, s: StatementSet, origin: int, balance: str,
                           driver: str, config: ForecastConfig,
                           as_of: date) -> ModelAssumption:
    latest = ratio_driver(driver_id, lookup(s, origin, balance, as_of),
                          lookup(s, origin, driver, as_of), allow_not_reported=True)
    if config.working_capital_basis == "latest" or latest.status in (NOT_APPLIED, GAP):
        return latest
    years = range(origin - config.median_years + 1, origin + 1)
    shares = [ratio_driver(driver_id, lookup(s, y, balance, as_of), lookup(s, y, driver, as_of))
              for y in years]
    usable_shares = [x for x in shares if x.status == SET]
    if len(usable_shares) < config.median_years:
        return gap(driver_id, [], f"median_3y needs {config.median_years} years; "
                                  + "; ".join(x.detail for x in shares if x.status == GAP))
    refs = tuple(r for x in usable_shares for r in x.inputs)
    return ModelAssumption(driver_id, median(x.value for x in usable_shares),
                           f"MEDIAN_{config.median_years}Y", SET, refs, flags=_flags(refs),
                           detail=f"median of {balance} / {driver}")


def tax_rate(s: StatementSet, origin: int, config: ForecastConfig,
             as_of: date) -> ModelAssumption:
    """Latest effective rate (tax / pre-tax basis), bounded; statutory when pre-tax <= 0."""
    tax = lookup(s, origin, "income_tax_expense", as_of)
    pretax = lookup_basis(s, origin, "pretax_basis", as_of)
    missing = [x for x in (tax, pretax) if not x.found]
    if missing:
        return gap("tax_rate", missing)
    refs = (tax.ref, pretax.ref)
    if pretax.value <= 0:
        return ModelAssumption("tax_rate", config.statutory_tax_rate, "STATUTORY_FALLBACK",
                               DEFAULT, refs, flags=(STATUTORY_FALLBACK,),
                               detail="historical pre-tax <= 0: statutory rate (decision 5)")
    raw = tax.value / pretax.value
    value = clamp(raw, config.tax_bounds)
    return ModelAssumption("tax_rate", value, "LATEST_EFFECTIVE",
                           BOUNDED if value != raw else SET, refs, raw_value=raw,
                           bounds=config.tax_bounds, flags=_flags(refs),
                           detail=f"income tax / {pretax.metric}")


def interest_bearing_balance(s: StatementSet, year: int,
                             as_of: date) -> tuple[list[Lookup], list[Lookup]]:
    """Funded debt + finance-lease liabilities at a year end (#76): (used, missing). A finance
    lease liability already inside funded debt is not added again; an item the filer never
    reports is left out (listed by the caller); an unaccepted one is missing (never 0)."""
    debt = lookup(s, year, "funded_debt", as_of)
    lease = lookup(s, year, "finance_lease_liability", as_of)
    included = debt.found and FINANCE_LEASES_INCLUDED in debt.ref.flags
    parts = [debt] + ([] if included else [lease])
    parts = [x for x in parts if not x.not_reported]
    return [x for x in parts if x.found], [x for x in parts if not x.found]


def interest_rate(s: StatementSet, origin: int, config: ForecastConfig,
                  as_of: date) -> ModelAssumption:
    """Effective rate = interest basis / average (funded debt + finance lease liabilities) over
    the origin year and the year before (#76); applied to debt and finance leases. Else the
    default rate (explicit assumption). The interest basis is gross interest expense: a filer
    that reports only net interest has none, so it gets the default (#73)."""
    interest = lookup_basis(s, origin, "interest_basis", as_of)
    closing, closing_missing = interest_bearing_balance(s, origin, as_of)
    opening, opening_missing = interest_bearing_balance(s, origin - 1, as_of)
    missing = ([] if interest.found else [interest]) + closing_missing + opening_missing
    average = (sum(x.value for x in closing) + sum(x.value for x in opening)) / 2 \
        if not missing else 0.0
    if missing or average <= 0:
        why = "; ".join(f"{m.metric} FY{m.fiscal_year}: {m.reason}" for m in missing) \
            or "average funded debt and finance leases is zero"
        if interest.reason == "NO_BASIS":
            why = "no gross interest expense reported; " + why
        return ModelAssumption("interest_rate", config.default_debt_rate, "DEFAULT_RATE",
                               DEFAULT, flags=(DEFAULT_RATE,),
                               detail=f"no effective rate ({why}): explicit assumption")
    refs = (interest.ref,) + tuple(x.ref for x in closing + opening)
    names = " + ".join(sorted({x.metric for x in closing + opening}))
    return ModelAssumption("interest_rate", interest.value / average, "EFFECTIVE", SET, refs,
                           flags=_flags(refs),
                           detail=f"{interest.metric} / average ({names}); debt and finance "
                                  "leases")


def lease_rate(driver_id: str, config: ForecastConfig) -> ModelAssumption:
    return ModelAssumption(driver_id, config.lease_rate, "DEFAULT_RATE", DEFAULT,
                           flags=(DEFAULT_RATE,), detail="lease rate: explicit assumption")


def scheduled_amounts(driver_id: str, s: StatementSet, origin: int, metrics: Sequence[str],
                      balance: Lookup, as_of: date) -> ModelAssumption:
    """Scheduled payments for years 1..n from the maturity tables (value = year-1 amount; the
    schedule is in detail and inputs). No balance reported -> NOT_APPLIED; a balance with no
    schedule -> GAP (never assumed to be zero)."""
    if balance.not_reported:
        return not_applied(driver_id, balance)
    found = [lookup(s, origin, m, as_of) for m in metrics]
    missing = [x for x in found if not x.found]
    if missing or not balance.found:
        return gap(driver_id, missing + ([] if balance.found else [balance]))
    refs = tuple(x.ref for x in found)
    return ModelAssumption(driver_id, found[0].value, "SCHEDULE", SET, refs,
                           detail="years 1-n: " + ", ".join(f"{x.value:.0f}" for x in found))


def debt_principal(s: StatementSet, origin: int, metrics: Sequence[str], balance: Lookup,
                   as_of: date) -> ModelAssumption:
    """Scheduled debt principal (#77b). With no maturity schedule, year 1 is the current portion
    of long-term debt from the balance sheet (flagged CURRENT_PORTION_FALLBACK); years 2+ have
    no source and stay a GAP (never assumed)."""
    found = scheduled_amounts("debt_principal", s, origin, metrics, balance, as_of)
    if found.status != GAP or not balance.found:
        return found
    years = [lookup(s, origin, m, as_of) for m in metrics]
    if years[0].found:
        return found
    current = lookup(s, origin, "long_term_debt_current", as_of)
    if not current.found:
        return found
    later = [x for x in years[1:] if not x.found]
    if not later:
        nil = tuple(f for f in current.ref.flags if f == NIL_UNREAD_YEAR)  # #152
        return ModelAssumption("debt_principal", current.value, "SCHEDULE", SET,
                               (current.ref,) + tuple(x.ref for x in years[1:]),
                               flags=(CURRENT_PORTION_FALLBACK,) + nil,
                               detail="year 1 = current portion of long-term debt; years 2-n: "
                                      + ", ".join(f"{x.value:.0f}" for x in years[1:]))
    why = "; ".join(f"{m.metric} FY{m.fiscal_year}: {m.reason}" for m in later)
    return ModelAssumption("debt_principal", None, "GAP", GAP, (current.ref,),
                           flags=(CURRENT_PORTION_FALLBACK,),
                           detail=f"year 1 = current portion of long-term debt "
                                  f"{current.value / 1e6:,.0f}m (fallback); no schedule for "
                                  f"later years ({why})")


def schedule_values(assumption: ModelAssumption) -> list[float]:
    return [float(r.value) for r in assumption.inputs]


def three_year_median(driver_id: str, values: Sequence[Lookup], config: ForecastConfig,
                      ratio_to: Sequence[Lookup] | None = None) -> ModelAssumption:
    """Median over the latest years (decision 3): all years, or >= min_years accepted years
    (flagged PARTIAL_HISTORY); fewer -> GAP. Never reported -> NOT_APPLIED. With ratio_to
    (payout), each year's ratio is value / income, for years with positive income only."""
    if all(v.not_reported for v in values):
        return not_applied(driver_id, values[0])
    points: list[tuple[float, tuple[InputRef, ...]]] = []
    flags: list[str] = []
    for index, v in enumerate(values):
        if not v.found:
            continue
        if ratio_to is None:
            points.append((v.value, (v.ref,)))
            continue
        income = ratio_to[index]
        if not income.found:
            continue
        if income.value <= 0:
            flags.append(NEGATIVE_INCOME_YEARS_EXCLUDED)
            continue
        points.append((v.value / income.value, (v.ref, income.ref)))
    if len(points) < config.min_median_years:
        missing = [v for v in values if not v.found] + \
            [x for x in (ratio_to or ()) if not x.found]
        why = "; ".join(f"{m.metric} FY{m.fiscal_year}: {m.reason}" for m in missing)
        return gap(driver_id, missing, f"{len(points)} usable year(s) of {len(values)}; needs "
                                       f"{config.min_median_years}" + (f" ({why})" if why else ""))
    if len(points) < len(values):
        flags.append(PARTIAL_HISTORY)
    refs = tuple(r for _, rs in points for r in rs)
    return ModelAssumption(driver_id, median(p for p, _ in points),
                           f"MEDIAN_{len(values)}Y", SET, refs,
                           flags=tuple(sorted(set(flags))),
                           detail=f"median of {len(points)} year(s)")
