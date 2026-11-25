"""Integrated three-statement forecast (blueprint 8.10; Phase 4b).

build_forecast(statement_set, as_of) projects the income statement, balance sheet and cash
flow three years from the latest fiscal year (the origin), using only accepted values of the
validated statement set and drivers that are MODEL_ASSUMPTION records (finagent.model.drivers).

How the balance sheet closes, and why it is not a plug:
- Precondition: I9 (assets = liabilities + equity) and I10 (cash roll-forward) PASS exactly in
  the origin year, so the opening balance sheet is the one the identities tested.
- Opening lines are accepted reported values; each "other" line is a CALCULATION from its own
  side's reported total (other assets = total assets - the listed assets; other liabilities =
  total liabilities - the listed liabilities; other equity = equity - retained earnings) and is
  held constant. None is ever solved for as assets - liabilities - equity.
- Every projected flow is posted twice: net income to retained earnings and to CFO;
  depreciation off PP&E and back in CFO; capex onto PP&E and out of cash; working-capital
  balances against CFO; principal off debt or lease and out of cash; operating lease principal
  off both the ROU asset and the liability (the cash payment is the period cost); dividends off
  retained earnings and cash; repurchases off other equity and cash; new debt onto debt and cash.
  Cash is the result of the cash flow statement. F1 then tests the arithmetic for real.
- Short-term investments are their own line (held constant, out of other assets); liquidity =
  cash + short-term investments (cash alone when the filer reports none).

Costs: cost of revenue share + other cash cost share; for a filer with no cost-of-sales line
that has a tag reading, one total cash operating cost share instead, with inventory and payables
as shares of revenue (basis NO_COGS, #77a). A document-only cost-of-revenue reading without an
accepted value is not used.

A missing input is never zero: a line the filer never reports does not exist (NOT_APPLIED,
flagged); any other missing input makes the forecast a GAP. Any failed check (F1-F4) makes it
INVALID with no projected numbers. Negative projected cash is allowed, shown and flagged as a
shortfall (decision 7), and so is negative liquidity; neither is funded by a plug or an
automatic revolver.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from finagent.config import (BASE_SCENARIO, CASH_ONLY, ForecastConfig, ScenarioSpec,
                             load_forecast_config)
from finagent.model import drivers as d
from finagent.model.checks import run_checks
from finagent.model.history import Lookup, check_point_in_time, lookup, lookup_basis
from finagent.model.shocks import shock_drivers
from finagent.records import Forecast, InputRef, ModelAssumption, OpeningLine, ProjectedYear
from finagent.statements.build import StatementSet

VERIFIED, INVALID, GAP = "VERIFIED", "INVALID", "GAP"
CASH_SHORTFALL = "CASH_SHORTFALL"
LIQUIDITY_SHORTFALL = "LIQUIDITY_SHORTFALL"  # cash + short-term investments < 0
HEADROOM_SHORTFALL = "HEADROOM_SHORTFALL"  # F5 headroom < 0 (shortfall > 0)
HEADROOM_NOT_COMPUTED = "HEADROOM_NOT_COMPUTED"  # liquidity unknown: no headroom, no F5
SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS = "SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS"
NO_TAX_BENEFIT = "NO_TAX_BENEFIT"  # projected pre-tax <= 0: tax 0, no benefit booked
FINANCE_LEASES_IN_DEBT = "FINANCE_LEASES_IN_DEBT"
FINANCE_LEASE_IN_OTHER_LIABILITIES = "FINANCE_LEASE_IN_OTHER_LIABILITIES"
LEASE_COST_EQUALS_PAYMENT = "LEASE_COST_EQUALS_PAYMENT"

ASSET_LINES = ("cash", "short_term_investments", "receivables", "inventory", "ppe",
               "operating_rou", "other_assets")
LIABILITY_LINES = ("payables", "debt", "operating_lease", "finance_lease", "other_liabilities")
EQUITY_LINES = ("retained_earnings", "other_equity")
BALANCE_LINES = ASSET_LINES + LIABILITY_LINES + EQUITY_LINES


@dataclass
class Opening:
    lines: dict[str, OpeningLine] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def add(self, line: str, value: float, refs: Sequence[InputRef],
            classification: str = "REPORTED_FACT", detail: str = "") -> None:
        self.lines[line] = OpeningLine(line, value, classification, tuple(refs), detail)

    def value(self, line: str) -> float:
        return self.lines[line].value

    def has(self, line: str) -> bool:
        return line in self.lines


def _identity_status(s: StatementSet, year: int, identity_id: str) -> str | None:
    found = [r for r in s.identities if r.fiscal_year == year and r.identity_id == identity_id]
    return found[0].status if found else None


def _optional_line(opening: Opening, line: str, found: Lookup) -> None:
    """A line the filer may not have: absent when never reported, a GAP when unaccepted."""
    if found.found:
        opening.add(line, found.value, [found.ref])
    elif not found.not_reported:
        opening.gaps.append(f"{found.metric} FY{found.fiscal_year}: {found.reason}")


def opening_position(s: StatementSet, origin: int, as_of: date) -> Opening:
    """The origin-year balance sheet, from accepted values and the equity terms of I9."""
    opening = Opening()
    for identity_id in ("I9", "I10"):
        status = _identity_status(s, origin, identity_id)
        if status != "PASS":
            opening.gaps.append(f"{identity_id} FY{origin} is {status}, not PASS (precondition)")
    get = lambda metric: lookup(s, origin, metric, as_of)  # noqa: E731
    i9 = next((r for r in s.identities if r.fiscal_year == origin and r.identity_id == "I9"),
              None)
    equity_terms = [term[1:] for term, _ in (i9.operands if i9 else ())
                    if term not in ("+total_assets", "-total_liabilities")]
    required = {"cash": get("cash_and_cash_equivalents"), "ppe": get("ppe_net"),
                "total_assets": get("total_assets"),
                "total_liabilities": get("total_liabilities"),
                "retained_earnings": get("retained_earnings")}
    equity = [get(m) for m in equity_terms]
    for name, found in list(required.items()) + [(e.metric, e) for e in equity]:
        if not found.found:
            opening.gaps.append(f"{found.metric} FY{origin}: {found.reason}")
    if not equity_terms:
        opening.gaps.append(f"I9 FY{origin} has no equity operands")
    receivables, inventory, payables = (get("accounts_receivable"), get("inventory"),
                                        get("accounts_payable"))
    rou, operating_lease = get("operating_lease_rou_asset"), get("operating_lease_liability")
    investments = get("short_term_investments")
    debt, finance_lease = get("funded_debt"), get("finance_lease_liability")
    for line, found in (("receivables", receivables), ("inventory", inventory),
                        ("payables", payables), ("operating_rou", rou),
                        ("operating_lease", operating_lease)):
        _optional_line(opening, line, found)
    if operating_lease.found != rou.found and not opening.gaps:
        opening.gaps.append("operating lease liability and ROU asset must both be reported")
    if opening.gaps:
        return opening
    # Short-term investments: a line of its own when accepted; never reported -> absent
    # (liquidity = cash); reported but unaccepted -> left inside other assets, liquidity not
    # computed (flagged; the balance is held constant either way).
    if investments.found:
        opening.add("short_term_investments", investments.value, [investments.ref])
    elif not investments.not_reported:
        opening.flags.append(f"{SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS}:{investments.reason}")
    opening.add("cash", required["cash"].value, [required["cash"].ref])
    opening.add("ppe", required["ppe"].value, [required["ppe"].ref])
    opening.add("retained_earnings", required["retained_earnings"].value,
                [required["retained_earnings"].ref])

    # Debt and finance leases: never counted twice when the debt concept includes leases.
    included = debt.found and any(f == "FINANCE_LEASES_INCLUDED" for f in debt.ref.flags)
    if debt.found:
        if included and finance_lease.found:
            opening.add("debt", debt.value - finance_lease.value, [debt.ref, finance_lease.ref],
                        "CALCULATION", "funded debt (includes finance leases) - finance lease "
                                       "liability")
            opening.add("finance_lease", finance_lease.value, [finance_lease.ref])
        else:
            opening.add("debt", debt.value, [debt.ref])
            if included:
                opening.flags.append(FINANCE_LEASES_IN_DEBT)
    elif not debt.not_reported:
        opening.gaps.append(f"funded_debt FY{origin}: {debt.reason}")
        return opening
    if not included:
        if finance_lease.found:
            opening.add("finance_lease", finance_lease.value, [finance_lease.ref])
        elif not finance_lease.not_reported:
            opening.flags.append(FINANCE_LEASE_IN_OTHER_LIABILITIES)

    assets = [l for l in ASSET_LINES[:-1] if opening.has(l)]
    total_assets = required["total_assets"]
    opening.add("other_assets", total_assets.value - sum(opening.value(l) for l in assets),
                [total_assets.ref], "CALCULATION", f"total assets - ({', '.join(assets)})")
    liabilities = [l for l in ("payables", "debt", "operating_lease", "finance_lease")
                   if opening.has(l)]
    total_liabilities = required["total_liabilities"]
    opening.add("other_liabilities",
                total_liabilities.value - sum(opening.value(l) for l in liabilities),
                [total_liabilities.ref], "CALCULATION",
                f"total liabilities - ({', '.join(liabilities)})")
    equity_total = sum(e.value for e in equity)
    opening.add("other_equity", equity_total - opening.value("retained_earnings"),
                [e.ref for e in equity], "CALCULATION",
                f"({' + '.join(equity_terms)}) - retained earnings")
    return opening


def no_cogs(s: StatementSet, origin: int) -> bool:
    """True when cost of revenue has no accepted value and no tag reading (#77a): the filer
    tags no cost-of-sales concept (NOT_REPORTED), or only a document-only reading exists (not
    used). A tag reading that is unaccepted (DISAGREE, CONFLICT, ...) is a GAP instead."""
    observation = s.get(origin, "cost_of_revenue")
    if observation is None:
        return True
    if observation.accepted and observation.value is not None:
        return False
    return "tag" not in observation.channels and observation.status not in ("DISAGREE",
                                                                            "CONFLICT")


def build_drivers(s: StatementSet, origin: int, opening: Opening, config: ForecastConfig,
                  as_of: date) -> dict[str, ModelAssumption]:
    """Every driver for the origin year (blueprint 8.10)."""
    get = lambda metric, year=origin: lookup(s, year, metric, as_of)  # noqa: E731
    found: dict[str, ModelAssumption] = {}

    def put(a: ModelAssumption) -> None:
        found[a.driver_id] = a

    put(d.revenue_growth(s, origin, config, as_of))
    if no_cogs(s, origin):
        put(d.cash_operating_cost_share(s, origin, as_of))
        cost_base, basis_flag = "revenue", d.NO_COGS
    else:
        put(d.ratio_driver("cogs_share", get("cost_of_revenue"), get("revenue")))
        put(d.other_cash_cost_share(s, origin, as_of))
        cost_base, basis_flag = "cost_of_revenue", None
    put(d.working_capital_share("receivables_share", s, origin, "accounts_receivable",
                                "revenue", config, as_of))
    put(d.working_capital_share("inventory_share", s, origin, "inventory", cost_base,
                                config, as_of, basis_flag))
    put(d.working_capital_share("payables_share", s, origin, "accounts_payable",
                                cost_base, config, as_of, basis_flag))
    put(d.ratio_driver("depreciation_rate", get("depreciation"), get("ppe_net"),
                       basis="LATEST_ON_CLOSING_PPE"))
    put(d.ratio_driver("capex_share", lookup_basis(s, origin, "capex_basis", as_of),
                       get("revenue")))
    put(d.tax_rate(s, origin, config, as_of))
    if opening.has("debt") or opening.has("finance_lease"):
        put(d.interest_rate(s, origin, config, as_of))
    if opening.has("debt"):
        principal = d.debt_principal(
            s, origin, [f"debt_principal_y{i}" for i in range(1, config.horizon_years + 1)],
            get("funded_debt"), as_of)
        put(principal)
        refinance = 1.0 if config.refinance_maturities else 0.0
        put(ModelAssumption(
            "refinanced_share_of_principal", refinance,
            "REFINANCE_MATURITIES" if config.refinance_maturities else "NO_NEW_DEBT", d.SET,
            detail="new debt = scheduled principal each year (switch on)"
            if config.refinance_maturities
            else "no new borrowing (default; refinance switch off): explicit assumption"))
    if opening.has("operating_lease"):
        put(d.lease_rate("operating_lease_rate", config))
        put(d.scheduled_amounts(
            "operating_lease_payments", s, origin,
            [f"operating_lease_payments_y{i}" for i in range(1, config.horizon_years + 1)],
            get("operating_lease_liability"), as_of))
    if opening.has("finance_lease"):
        rate = found["interest_rate"]
        # The effective rate covers debt and finance leases (#76); the separate default lease
        # rate only when there is no effective rate.
        put(ModelAssumption("finance_lease_rate", rate.value, "INTEREST_RATE", rate.status,
                            rate.inputs, flags=rate.flags, detail="= interest_rate (#76)")
            if rate.status == d.SET else d.lease_rate("finance_lease_rate", config))
        put(d.scheduled_amounts(
            "finance_lease_payments", s, origin,
            [f"finance_lease_payments_y{i}" for i in range(1, config.horizon_years + 1)],
            get("finance_lease_liability"), as_of))
    years = list(range(origin - config.median_years + 1, origin + 1))
    put(d.three_year_median("dividend_payout_ratio", [get("dividends_paid", y) for y in years],
                            config, ratio_to=[get("net_income", y) for y in years]))
    put(d.three_year_median("repurchases", [get("repurchases_paid", y) for y in years], config))
    return found


def _value(assumptions: dict[str, ModelAssumption], driver_id: str) -> float | None:
    a = assumptions.get(driver_id)
    return None if a is None or a.status in (d.NOT_APPLIED, d.GAP) else a.value


def project(origin: int, opening: Opening, a: dict[str, ModelAssumption],
            config: ForecastConfig, last_revenue: float,
            scenario: ScenarioSpec = BASE_SCENARIO) -> list[ProjectedYear]:
    """Three projected years. Lines the filer does not have are absent, never 0. The scenario
    only sets the liquidity headroom (haircut, floor); its driver shocks are already in a."""
    state = {line: o.value for line, o in opening.lines.items()}
    revenue_prev = last_revenue
    debt_schedule = d.schedule_values(a["debt_principal"]) if _value(a, "debt_principal") \
        is not None else []
    op_schedule = d.schedule_values(a["operating_lease_payments"]) \
        if _value(a, "operating_lease_payments") is not None else []
    fin_schedule = d.schedule_values(a["finance_lease_payments"]) \
        if _value(a, "finance_lease_payments") is not None else []
    payout = _value(a, "dividend_payout_ratio")
    # Liquidity = cash + short-term investments; not computed when the investments are reported
    # but unaccepted (left in other assets). A filer with none: cash alone.
    liquidity_known = not any(f.startswith(SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS)
                              for f in opening.flags)
    repurchases = _value(a, "repurchases")
    years: list[ProjectedYear] = []
    for i in range(config.horizon_years):
        old = dict(state)
        lines: dict[str, float] = {}
        flags: list[str] = []
        revenue = revenue_prev * (1 + a["revenue_growth"].value)
        depreciation = old["ppe"] * a["depreciation_rate"].value
        lines.update(revenue=revenue, depreciation=depreciation)
        if "cash_operating_cost_share" in a:  # NO_COGS: one total cash operating cost
            cash_costs = revenue * a["cash_operating_cost_share"].value
            lines["cash_operating_costs"] = cash_costs
            cost_base = revenue
        else:
            cogs = revenue * a["cogs_share"].value
            other = revenue * a["other_cash_cost_share"].value
            lines.update(cogs=cogs, other_cash_costs=other)
            cash_costs, cost_base = cogs + other, cogs
        ebit = revenue - cash_costs - depreciation
        interest = 0.0
        principal = 0.0
        if "operating_lease" in old:
            payment = op_schedule[i]
            lease_interest = old["operating_lease"] * a["operating_lease_rate"].value
            lease_principal = payment - lease_interest
            lines.update(operating_lease_cost=payment, operating_lease_interest=lease_interest,
                         operating_lease_principal=lease_principal)
            ebit -= payment
            state["operating_rou"] = old["operating_rou"] - lease_principal
            state["operating_lease"] = old["operating_lease"] - lease_principal
        lines["ebit"] = ebit
        if "debt" in old:
            debt_interest = old["debt"] * a["interest_rate"].value
            scheduled = debt_schedule[i]
            new_debt = scheduled if config.refinance_maturities else 0.0
            lines.update(debt_interest=debt_interest, debt_principal=scheduled,
                         new_debt=new_debt)
            interest += debt_interest
            principal += scheduled
            state["debt"] = old["debt"] - scheduled + new_debt
        else:
            new_debt = 0.0
        if "finance_lease" in old:
            payment = fin_schedule[i]
            fin_interest = old["finance_lease"] * a["finance_lease_rate"].value
            fin_principal = payment - fin_interest
            lines.update(finance_lease_interest=fin_interest,
                         finance_lease_principal=fin_principal)
            interest += fin_interest
            principal += fin_principal
            state["finance_lease"] = old["finance_lease"] - fin_principal
        pretax = ebit - interest
        if pretax > 0:
            tax = pretax * a["tax_rate"].value
        else:
            tax = 0.0
            flags.append(NO_TAX_BENEFIT)
        net_income = pretax - tax
        lines.update(interest=interest, pretax=pretax, tax=tax, cash_tax=tax,
                     net_income=net_income)
        dividends = payout * max(net_income, 0.0) if payout is not None else None
        capex = revenue * a["capex_share"].value
        for line, share, base in (("receivables", "receivables_share", revenue),
                                  ("inventory", "inventory_share", cost_base),
                                  ("payables", "payables_share", cost_base)):
            if line in old:
                state[line] = base * a[share].value
        working_capital = -(sum(state[l] - old[l] for l in ("receivables", "inventory")
                                if l in old)
                            - (state["payables"] - old["payables"] if "payables" in old else 0))
        state["ppe"] = old["ppe"] + capex - depreciation
        state["retained_earnings"] = old["retained_earnings"] + net_income - (dividends or 0.0)
        state["other_equity"] = old["other_equity"] - (repurchases or 0.0)
        cfo = net_income + depreciation + working_capital
        cfi = -capex
        cff = new_debt - principal - (dividends or 0.0) - (repurchases or 0.0)
        state["cash"] = old["cash"] + cfo + cfi + cff
        if dividends is not None:
            lines["dividends"] = dividends
        if repurchases is not None:
            lines["repurchases"] = repurchases
        lines.update(capex=capex, working_capital_cf=working_capital, cfo=cfo, cfi=cfi, cff=cff,
                     principal=principal)
        for line in BALANCE_LINES:
            if line in state:
                lines[line] = state[line]
        if state["cash"] < 0:
            flags.append(CASH_SHORTFALL)
        if liquidity_known:
            lines["liquidity"] = state["cash"] + state.get("short_term_investments", 0.0)
            if lines["liquidity"] < 0:
                flags.append(LIQUIDITY_SHORTFALL)
        # F5 headroom on the configured basis (#154): cash + short-term investments x
        # (1 - haircut) - floor, which needs known liquidity, or cash - floor (8.12).
        if config.headroom_basis == CASH_ONLY or liquidity_known:
            securities = 0.0 if config.headroom_basis == CASH_ONLY else \
                state.get("short_term_investments", 0.0) * (1 - scenario.securities_haircut)
            headroom = state["cash"] + securities - scenario.minimum_cash_floor
            lines["liquidity_headroom"] = headroom
            lines["liquidity_shortfall"] = max(0.0, -headroom)
            if headroom < 0:
                flags.append(HEADROOM_SHORTFALL)
        else:
            flags.append(HEADROOM_NOT_COMPUTED)
        lines["total_assets"] = sum(state[l] for l in ASSET_LINES if l in state)
        lines["total_liabilities"] = sum(state[l] for l in LIABILITY_LINES if l in state)
        lines["total_equity"] = sum(state[l] for l in EQUITY_LINES if l in state)
        years.append(ProjectedYear(origin + i + 1, lines, tuple(flags)))
        revenue_prev = revenue
    return years


def _gap_forecast(s: StatementSet, as_of: date, origin: int | None, status: str,
                  reasons: Sequence[str], assumptions=(), opening=(), checks=(),
                  switches=None, flags=(), scenario: str = "Base") -> Forecast:
    return Forecast(s.issuer, s.cik, as_of, origin, status, tuple(reasons), tuple(assumptions),
                    tuple(opening), (), tuple(checks), dict(switches or {}), tuple(flags),
                    scenario)


def build_forecast(statement_set: StatementSet, as_of: date,
                   config: ForecastConfig | None = None,
                   scenario: ScenarioSpec = BASE_SCENARIO) -> Forecast:
    """The integrated forecast from the latest fiscal year of the set, as of `as_of`, under one
    scenario (8.11; Base = no shock, the 4b forecast)."""
    config = config or load_forecast_config()
    name = scenario.name
    check_point_in_time(statement_set, as_of)
    switches = {"working_capital_basis": config.working_capital_basis,
                "refinance_maturities": config.refinance_maturities,
                "headroom_basis": config.headroom_basis}
    if not statement_set.filings:
        return _gap_forecast(statement_set, as_of, None, GAP, ["no filings"], switches=switches,
                             scenario=name)
    origin = max(statement_set.years())
    opening = opening_position(statement_set, origin, as_of)
    if opening.gaps:
        return _gap_forecast(statement_set, as_of, origin, GAP, opening.gaps,
                             opening=opening.lines.values(), switches=switches,
                             flags=opening.flags, scenario=name)
    assumptions = build_drivers(statement_set, origin, opening, config, as_of)
    gaps = [f"{a.driver_id}: {a.detail}" for a in assumptions.values() if a.status == d.GAP]
    flags = list(opening.flags)
    if gaps:
        return _gap_forecast(statement_set, as_of, origin, GAP, gaps, assumptions.values(),
                             opening.lines.values(), switches=switches, flags=flags,
                             scenario=name)
    assumptions, problems = shock_drivers(assumptions, scenario,
                                          "cash_operating_cost_share" in assumptions)
    if problems:
        return _gap_forecast(statement_set, as_of, origin, GAP, problems, assumptions.values(),
                             opening.lines.values(), switches=switches, flags=flags,
                             scenario=name)
    if "operating_lease" in opening.lines:
        flags.append(LEASE_COST_EQUALS_PAYMENT)
    last_revenue = lookup(statement_set, origin, "revenue", as_of).value
    years = project(origin, opening, assumptions, config, last_revenue, scenario)
    checks = run_checks(opening, years, config.tolerance, scenario.securities_haircut,
                        scenario.minimum_cash_floor, config.headroom_basis)
    failed = [f"{c.check_id} FY{c.fiscal_year}: residual {c.residual:.6g} ({c.detail})"
              for c in checks if c.status == "FAIL"]
    for flag in (f for y in years for f in y.flags):
        if flag not in flags:
            flags.append(flag)
    if failed:
        return _gap_forecast(statement_set, as_of, origin, INVALID, failed,
                             assumptions.values(), opening.lines.values(), checks, switches,
                             flags, scenario=name)
    return Forecast(statement_set.issuer, statement_set.cik, as_of, origin, VERIFIED, (),
                    tuple(assumptions.values()), tuple(opening.lines.values()), tuple(years),
                    tuple(checks), switches, tuple(flags), name)
