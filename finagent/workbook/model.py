"""Block B of the statement sheets and the Forecast sheet: the integrated model as live formulas
(blueprint 8.10, 8.11; Phase 4d; BC21).

The Forecast sheet holds the MODEL_ASSUMPTION drivers (blue, with basis, status and flags), the
scenario table of configs/scenarios.yaml with a selector cell, the effective (scenario) drivers
as formulas, the 10-K maturity schedules (blue REPORTED_FACT inputs), and the working-capital,
PP&E, debt, lease, equity and liquidity schedules with checks F2-F5. The statement sheets' model
blocks hold the projected income statement, balance sheet (with F1) and cash flow (with the cash
tie-out). FY0 is the opening position, linked to the reported cells; FY+1..FY+3 are formulas
only. The formulas mirror finagent.model.forecast.project line by line; every flow is posted
twice and cash is the result of the cash flow, so the balance sheet balances without a plug.

Every formula cell carries the Python model's value per scenario as its expected value.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from finagent.config import ForecastConfig, ScenarioSpec
from finagent.model.forecast import SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS, VERIFIED
from finagent.model.shocks import shock_drivers
from finagent.records import Forecast, ModelAssumption
from finagent.workbook.grid import (ALL, BANNER_FILL, BOLD, GREY, INPUT_FILL, WRAP, Expected,
                                    Grid, section)

FORECAST = "Forecast"
IS, BS, CF = "Income Statement", "Balance Sheet", "Cash Flow"

SHOCKS = (  # (field, label, format)
    ("revenue_growth_shift", "Revenue growth shift (added each year; compounds)", "percent"),
    ("cogs_share_shift", "COGS share shift (total cash cost share for NO_COGS, BC20)", "percent"),
    ("dso_days", "DSO shift (days)", "days"),
    ("dio_days", "DIO shift (days)", "days"),
    ("dpo_days", "DPO shift (days)", "days"),
    ("funded_debt_rate_shift", "Funded debt interest rate shift", "percent"),
    ("capex_multiplier", "Capex multiplier", "days"),
    ("securities_haircut", "Haircut on short-term securities (liquidity)", "percent"),
    ("minimum_cash_floor", "Minimum cash floor ($)", "dollars"),
)
# driver -> (shock field, how): "add", "days" (add field / days in year) or "mul"
DRIVER_SHOCKS = {
    "revenue_growth": ("revenue_growth_shift", "add"),
    "cogs_share": ("cogs_share_shift", "add"),
    "cash_operating_cost_share": ("cogs_share_shift", "add"),
    "receivables_share": ("dso_days", "days"),
    "inventory_share": ("dio_days", "days"),
    "payables_share": ("dpo_days", "days"),
    "interest_rate": ("funded_debt_rate_shift", "add"),
    "capex_share": ("capex_multiplier", "mul"),
}
SCHEDULE_DRIVERS = {"debt_principal": "Debt principal (10-K maturities)",
                    "operating_lease_payments": "Operating lease payments (10-K maturities)",
                    "finance_lease_payments": "Finance lease payments (10-K maturities)"}
RATE_FORMAT = {"revenue_growth", "cogs_share", "other_cash_cost_share",
               "cash_operating_cost_share", "receivables_share", "inventory_share",
               "payables_share", "depreciation_rate", "capex_share", "tax_rate", "interest_rate",
               "operating_lease_rate", "finance_lease_rate", "dividend_payout_ratio",
               "securities_haircut", "refinanced_share_of_principal"}


def usable_driver(a: ModelAssumption | None) -> bool:
    return a is not None and a.status not in ("NOT_APPLIED", "GAP") and a.value is not None


@dataclass
class ModelContext:
    """What the formulas need from the Python model: Base drivers and opening position, and the
    projected lines of every VERIFIED scenario (the expected values)."""

    base: Forecast
    forecasts: Mapping[str, Forecast]
    specs: Sequence[ScenarioSpec]
    config: ForecastConfig
    last_revenue: float
    equity_terms: Sequence[str]  # signed I9 equity operands of the origin year

    @property
    def drivers(self) -> dict[str, ModelAssumption]:
        return {a.driver_id: a for a in self.base.assumptions}

    @property
    def opening(self) -> dict[str, float]:
        return {o.line: o.value for o in self.base.opening}

    def has(self, line: str) -> bool:
        return line in self.opening

    @property
    def no_cogs(self) -> bool:
        return "cash_operating_cost_share" in self.drivers

    @property
    def payout(self) -> bool:
        return usable_driver(self.drivers.get("dividend_payout_ratio"))

    @property
    def repurchases(self) -> bool:
        return usable_driver(self.drivers.get("repurchases"))

    @property
    def liquidity_known(self) -> bool:
        return not any(f.startswith(SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS)
                       for f in self.base.flags)

    @property
    def origin(self) -> int:
        assert self.base.origin_year is not None
        return self.base.origin_year

    @property
    def verified(self) -> list[str]:
        return [n for n, f in self.forecasts.items() if f.status == VERIFIED]

    def line(self, scenario: str, year: int, line: str) -> float:
        f = self.forecasts[scenario]
        return f.years[year - self.origin - 1].lines[line]

    def expect(self, year: int, fn: Callable[[str, int], float | str]) -> Expected:
        """Expected value per VERIFIED scenario (year > origin) or shared (origin)."""
        if year == self.origin:
            return {ALL: fn(self.base.scenario, year)}
        return {n: fn(n, year) for n in self.verified}

    def projected(self, line: str) -> Callable[[str, int], float]:
        """A projected line, or its opening value at the origin."""
        def get(scenario: str, year: int) -> float:
            if year == self.origin:
                return self.opening[line]
            return self.line(scenario, year, line)
        return get


class ModelWriter:
    """Writes the Forecast sheet and the three model blocks for one VERIFIED Base forecast."""

    def __init__(self, grid: Grid, ctx: ModelContext, sheets: Mapping[str, Worksheet],
                 start_rows: Mapping[str, int]) -> None:
        self.g, self.ctx, self.ws = grid, ctx, sheets
        self.row = dict(start_rows)
        self.origin = ctx.origin
        self.proj = grid.cols.proj
        self.lines: list[tuple[str, str, int, list[int], Callable[[int], tuple[str, Expected]],
                               str, str]] = []
        self.status_cells: list[str] = []

    # ---------------------------------------------------------------- planning helpers
    def add(self, sheet: str, key: str, label: str, years: Sequence[int],
            build: Callable[[int], tuple[str, Expected]], fmt: str = "dollars",
            tol: str = "dollar") -> None:
        r = self.row[sheet]
        self.row[sheet] += 1
        for y in years:
            self.g.register(key, y, sheet, f"{self.g.cols.letter(y)}{r}")
        self.lines.append((sheet, key, r, list(years), build, label, fmt + "|" + tol))

    def gap_row(self, sheet: str, n: int = 1) -> None:
        self.row[sheet] += n

    def heading(self, sheet: str, text: str) -> None:
        r = self.row[sheet]
        section(self.ws[sheet], r, text, self.g.cols.note)
        self.row[sheet] += 1

    def R(self, key: str, year: int | None, sheet: str) -> str:  # noqa: N802
        return self.g.ref(key, year, sheet)

    def D(self, driver: str, sheet: str) -> str:  # noqa: N802
        return self.g.absolute(f"d:{driver}", None, sheet)

    @property
    def all_years(self) -> list[int]:
        return [self.origin] + self.proj

    def prev(self, year: int) -> int:
        p = self.g.cols.previous(year)
        assert p is not None
        return p

    # ---------------------------------------------------------------- write
    def write_lines(self) -> None:
        for sheet, key, r, years, build, label, fmt_tol in self.lines:
            ws = self.ws[sheet]
            fmt, tol = fmt_tol.split("|")
            ws.cell(row=r, column=1, value=label)
            ws.cell(row=r, column=2, value="MODEL" if not key.startswith("chk") else "CHECK")
            for y in years:
                formula, expected = build(y)
                self.g.formula(ws, r, self.g.cols.col(y), formula, expected, fmt=fmt,
                               tolerance=tol)
                if key.endswith("_status"):
                    self.status_cells.append(self.g.ref(key, y, FORECAST))

    # ---------------------------------------------------------------- Forecast: inputs
    def forecast_inputs(self, top: int) -> int:
        """Selector, scenario table, switches, drivers and maturity schedules; returns the next
        free row. Registers d:* keys."""
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        names = [s.name for s in ctx.specs]
        r = top
        ws.cell(row=r, column=1, value="Scenario selector (Base / Moderate / Severe)").font = BOLD
        sel = g.value(ws, r, 3, ctx.base.scenario, key="d:selector", fmt="text")
        ws[sel].fill = INPUT_FILL
        dv = DataValidation(type="list", formula1='"' + ",".join(names) + '"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(sel)
        ws.cell(row=r, column=4, value="Change this cell: every shocked driver, all three "
                                       "statements and the checks recalculate.").font = GREY
        r += 1
        ws.cell(row=r, column=1, value="Scenario status")
        self.scenario_status_row = r
        r += 2
        section(ws, r, "Scenario table (configs/scenarios.yaml; MODEL_ASSUMPTION, blueprint 8.11)",
                g.cols.note)
        r += 1
        ws.cell(row=r, column=1, value="Shock").font = BOLD
        for i, n in enumerate(names):
            ws.cell(row=r, column=3 + i, value=n).font = BOLD
        sel_col = 3 + len(names)
        ws.cell(row=r, column=sel_col, value="Selected").font = BOLD
        header = r
        r += 1
        first_name = f"$C${header}"
        last_name = f"${_col(2 + len(names))}${header}"
        for field, label, fmt in SHOCKS:
            ws.cell(row=r, column=1, value=label)
            ws.cell(row=r, column=2, value="MA")
            for i, spec in enumerate(ctx.specs):
                g.value(ws, r, 3 + i, float(getattr(spec, field)), fmt=fmt)
            rng = f"$C${r}:${_col(2 + len(names))}${r}"
            g.formula(ws, r, sel_col,
                      f"INDEX({rng},MATCH({g.absolute('d:selector', None, FORECAST)},"
                      f"{first_name}:{last_name},0))",
                      {s.name: float(getattr(s, field)) for s in ctx.specs},
                      key=f"d:shock:{field}", fmt=fmt, tolerance="ratio")
            r += 1
        ws.cell(row=r, column=1, value="Days in year (days shocks -> shares)")
        g.value(ws, r, 3, float(ctx.specs[0].days_in_year), key="d:days", fmt="days")
        r += 2
        section(ws, r, "Switches and tolerance", g.cols.note)
        r += 1
        ws.cell(row=r, column=1, value="Refinance maturities (1 = new debt equals scheduled "
                                       "principal; 0 = no new debt)")
        g.value(ws, r, 3, 1.0 if ctx.config.refinance_maturities else 0.0, key="d:refinance",
                fmt="days")
        r += 1
        ws.cell(row=r, column=1, value="Working-capital basis (driver values below use it)")
        ws.cell(row=r, column=3, value=str(ctx.base.switches.get("working_capital_basis")))
        r += 1
        ws.cell(row=r, column=1, value="Check tolerance ($; Appendix B F1-F3, F5)")
        g.value(ws, r, 3, float(ctx.config.tolerance), key="d:tolerance", fmt="days")
        r += 2
        r = self.drivers_table(r)
        r = self.schedule_inputs(r + 1)
        return r + 1

    def drivers_table(self, r: int) -> int:
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        section(ws, r, "Drivers (MODEL_ASSUMPTION, blueprint 8.10): base value in blue; "
                       "scenario value = base + selected shock", g.cols.note)
        r += 1
        for c, text in enumerate(("Driver", "", "Base value", "Scenario value", "Basis",
                                  "Status", "Flags", "Note"), start=1):
            ws.cell(row=r, column=c, value=text).font = BOLD
        r += 1
        self.shocked_cells: list[str] = []
        for a in ctx.base.assumptions:
            ws.cell(row=r, column=1, value=a.driver_id)
            ws.cell(row=r, column=2, value="MA")
            fmt = "rate" if a.driver_id in RATE_FORMAT else "dollars"
            if a.driver_id in SCHEDULE_DRIVERS:
                fmt = "dollars"
            if usable_driver(a):
                base = g.value(ws, r, 3, float(a.value), key=f"d:base:{a.driver_id}", fmt=fmt)
                self._scenario_value(ws, r, a, base, fmt)
            else:
                g.empty(ws, r, 3, f"{a.status}: {a.detail}")
                ws.cell(row=r, column=3, value=a.status).font = GREY
            ws.cell(row=r, column=5, value=a.basis)
            ws.cell(row=r, column=6, value=a.status)
            ws.cell(row=r, column=7, value=", ".join(a.flags))
            note = a.detail
            if a.raw_value is not None and a.raw_value != a.value:
                note = f"raw {a.raw_value:.6g}; {note}"
            if a.inputs:
                note += "; inputs: " + ", ".join(f"{i.metric} FY{i.fiscal_year} {i.status}"
                                                 for i in a.inputs[:6])
            ws.cell(row=r, column=8, value=note).alignment = WRAP
            r += 1
        return r

    def _scenario_value(self, ws: Worksheet, r: int, a: ModelAssumption, base: str,
                        fmt: str) -> None:
        g, ctx = self.g, self.ctx
        # Expected: the Python shock of the Base driver (finagent.model.shocks), so it holds
        # whether or not the scenario's forecast ran (a GAP forecast stops before shocking).
        expected: Expected = {}
        for spec in ctx.specs:
            shocked, _ = shock_drivers(ctx.drivers, spec, ctx.no_cogs)
            value = shocked[a.driver_id].value
            if value is not None:
                expected[spec.name] = value
        if a.driver_id in ("securities_haircut", "minimum_cash_floor"):
            formula = g.absolute(f"d:shock:{a.driver_id}", None, FORECAST)
        elif a.driver_id in DRIVER_SHOCKS:
            field, how = DRIVER_SHOCKS[a.driver_id]
            shock = g.absolute(f"d:shock:{field}", None, FORECAST)
            formula = {"add": f"{base}+{shock}", "mul": f"{base}*{shock}",
                       "days": f"{base}+{shock}/{g.absolute('d:days', None, FORECAST)}"}[how]
            if a.driver_id != "revenue_growth":
                self.shocked_cells.append(f"D{r}")
        else:
            formula = base
        g.formula(ws, r, 4, formula, expected, key=f"d:{a.driver_id}", fmt=fmt,
                  tolerance="ratio" if fmt == "rate" else "dollar")

    def schedule_inputs(self, r: int) -> int:
        """The 10-K maturity schedules: blue REPORTED_FACT inputs for years 1..n."""
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        section(ws, r, "Maturity schedules (10-K, FY0 filing; REPORTED_FACT inputs)", g.cols.note)
        r += 1
        for i, y in enumerate(self.proj):
            ws.cell(row=r, column=3 + i, value=f"Year {i + 1} (FY{y})").font = BOLD
        r += 1
        for driver_id, label in SCHEDULE_DRIVERS.items():
            a = ctx.drivers.get(driver_id)
            if not usable_driver(a):
                continue
            assert a is not None
            ws.cell(row=r, column=1, value=label)
            ws.cell(row=r, column=2, value="RF")
            for i, (y, ref) in enumerate(zip(self.proj, a.inputs)):
                g.value(ws, r, 3 + i, float(ref.value), key=f"s:{driver_id}", year=y,
                        note=f"{ref.metric} FY{ref.fiscal_year} {ref.status}"
                             + (f" {', '.join(ref.flags)}" if ref.flags else ""))
            ws.cell(row=r, column=3 + len(self.proj), value=", ".join(a.flags)).font = GREY
            r += 1
        return r

    def scenario_status(self) -> None:
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        expected = {}
        for spec in ctx.specs:
            f = ctx.forecasts.get(spec.name)
            problems = [x for x in (f.reasons if f else ()) if "below zero" in x]
            expected[spec.name] = "GAP: a shocked share or rate is below zero" if problems \
                else "OK"
        cells = ",".join(f"${c[0]}${c[1:]}" for c in self.shocked_cells)
        formula = (f'IF(MIN({cells})<0,"GAP: a shocked share or rate is below zero","OK")'
                   if cells else '"OK"')
        g.formula(ws, self.scenario_status_row, 3, formula, expected, fmt="text",
                  tolerance="exact")

    # ---------------------------------------------------------------- plan every model line
    def plan(self) -> None:
        self.plan_forecast_schedules()
        self.plan_income_statement()
        self.plan_balance_sheet()
        self.plan_cash_flow()
        self.plan_checks()

    def plan_forecast_schedules(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        P = ctx.projected  # noqa: N806
        y0, proj, every = self.origin, self.proj, self.all_years

        def opening(key: str, line: str) -> Callable[[int], tuple[str, Expected]]:
            return lambda y: (self.R(key, self.prev(y), F),
                              ctx.expect(y, lambda n, yy: P(line)(n, self.prev(yy))))

        # Working capital
        self.heading(F, "Working capital (shares of revenue, or of COGS / revenue for NO_COGS)")
        base_key = "m:revenue" if ctx.no_cogs else "m:cogs"
        for line, metric, driver, base in (
                ("receivables", "accounts_receivable", "receivables_share", "m:revenue"),
                ("inventory", "inventory", "inventory_share", base_key),
                ("payables", "accounts_payable", "payables_share", base_key)):
            if not ctx.has(line):
                continue
            self.add(F, f"f:{line}", line.capitalize(), every,
                     self._wc(line, metric, driver, base))
        self.add(F, "f:wc_cf", "Working-capital cash flow (-(change in receivables + "
                               "inventory - payables))", proj, self._wc_cf)
        self.gap_row(F)
        # PP&E
        self.heading(F, "PP&E")
        self.add(F, "f:ppe_open", "Opening PP&E", proj, opening("f:ppe", "ppe"))
        self.add(F, "f:capex", "Capex (revenue x capex share)", proj,
                 lambda y: (f"{self.R('m:revenue', y, F)}*{self.D('capex_share', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "capex"))))
        self.add(F, "f:depreciation", "Depreciation (opening PP&E x rate)", proj,
                 lambda y: (f"{self.R('f:ppe_open', y, F)}*{self.D('depreciation_rate', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "depreciation"))))
        self.add(F, "f:ppe", "Closing PP&E", every,
                 lambda y: (self.R("r:ppe_net", y, F), ctx.expect(y, P("ppe"))) if y == y0 else
                 (f"{self.R('f:ppe_open', y, F)}+{self.R('f:capex', y, F)}"
                  f"-{self.R('f:depreciation', y, F)}", ctx.expect(y, P("ppe"))))
        self.gap_row(F)
        if ctx.has("debt"):
            self.plan_debt()
        if ctx.has("finance_lease"):
            self.plan_lease("finance_lease", "fl", "Finance lease", "finance_lease_rate",
                            "finance_lease_payments", "finance_lease_liability")
        if ctx.has("operating_lease"):
            self.plan_lease("operating_lease", "ol", "Operating lease", "operating_lease_rate",
                            "operating_lease_payments", "operating_lease_liability")
        self.plan_equity()
        if ctx.liquidity_known:
            self.plan_liquidity()

    def _wc(self, line: str, metric: str, driver: str, base: str):
        ctx, F = self.ctx, FORECAST  # noqa: N806

        def build(y: int) -> tuple[str, Expected]:
            if y == self.origin:
                return self.R(f"r:{metric}", y, F), ctx.expect(y, ctx.projected(line))
            return (f"{self.R(base, y, F)}*{self.D(driver, F)}",
                    ctx.expect(y, ctx.projected(line)))
        return build

    def _wc_cf(self, y: int) -> tuple[str, Expected]:
        F, p = FORECAST, self.prev(y)  # noqa: N806
        up = [f"({self.R(f'f:{l}', y, F)}-{self.R(f'f:{l}', p, F)})"
              for l in ("receivables", "inventory") if self.ctx.has(l)]
        formula = "-(" + ("+".join(up) if up else "0")
        if self.ctx.has("payables"):
            formula += f"-({self.R('f:payables', y, F)}-{self.R('f:payables', p, F)})"
        formula += ")"
        return formula, self.ctx.expect(y, lambda n, yy: self.ctx.line(n, yy,
                                                                        "working_capital_cf"))

    def plan_debt(self) -> None:
        ctx, F, P = self.ctx, FORECAST, self.ctx.projected  # noqa: N806
        self.heading(F, "Funded debt (scheduled principal from the 10-K maturity table)")
        opening_line = {o.line: o for o in ctx.base.opening}["debt"]

        def closing(y: int) -> tuple[str, Expected]:
            if y == self.origin:
                if opening_line.classification == "CALCULATION":
                    return (f"{self.R('r:funded_debt', y, F)}"
                            f"-{self.R('r:finance_lease_liability', y, F)}",
                            ctx.expect(y, P("debt")))
                return self.R("r:funded_debt", y, F), ctx.expect(y, P("debt"))
            return (f"{self.R('f:debt_open', y, F)}-{self.R('f:debt_principal', y, F)}"
                    f"+{self.R('f:new_debt', y, F)}", ctx.expect(y, P("debt")))

        self.add(F, "f:debt_open", "Opening debt", self.proj,
                 lambda y: (self.R("f:debt", self.prev(y), F),
                            ctx.expect(y, lambda n, yy: P("debt")(n, self.prev(yy)))))
        self.add(F, "f:debt_principal", "Scheduled principal", self.proj,
                 lambda y: (self.R("s:debt_principal", y, F),
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "debt_principal"))))
        self.add(F, "f:new_debt", "New debt (scheduled principal x refinance switch)", self.proj,
                 lambda y: (f"{self.R('f:debt_principal', y, F)}*{self.D('refinance', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "new_debt"))))
        self.add(F, "f:debt", "Closing debt", self.all_years, closing)
        self.add(F, "f:debt_interest", "Interest on debt (opening x scenario rate)", self.proj,
                 lambda y: (f"{self.R('f:debt_open', y, F)}*{self.D('interest_rate', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "debt_interest"))))
        self.gap_row(F)

    def plan_lease(self, line: str, short: str, title: str, rate: str, schedule: str,
                   metric: str) -> None:
        ctx, F, P = self.ctx, FORECAST, self.ctx.projected  # noqa: N806
        self.heading(F, f"{title} (payments from the 10-K maturity table)")
        self.add(F, f"f:{short}_open", f"Opening {title.lower()} liability", self.proj,
                 lambda y: (self.R(f"f:{line}", self.prev(y), F),
                            ctx.expect(y, lambda n, yy: P(line)(n, self.prev(yy)))))
        self.add(F, f"f:{short}_payment", "Scheduled payment", self.proj,
                 lambda y: (self.R(f"s:{schedule}", y, F),
                            ctx.expect(y, lambda n, yy: ctx.line(
                                n, yy, "operating_lease_cost") if line == "operating_lease"
                                else ctx.line(n, yy, "finance_lease_interest")
                                + ctx.line(n, yy, "finance_lease_principal"))))
        self.add(F, f"f:{short}_interest", "Interest (opening x rate)", self.proj,
                 lambda y: (f"{self.R(f'f:{short}_open', y, F)}*{self.D(rate, F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, f"{line}_interest"))))
        self.add(F, f"f:{short}_principal", "Principal (payment - interest)", self.proj,
                 lambda y: (f"{self.R(f'f:{short}_payment', y, F)}"
                            f"-{self.R(f'f:{short}_interest', y, F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, f"{line}_principal"))))
        self.add(F, f"f:{line}", f"Closing {title.lower()} liability", self.all_years,
                 lambda y: (self.R(f"r:{metric}", y, F), ctx.expect(y, P(line)))
                 if y == self.origin else
                 (f"{self.R(f'f:{short}_open', y, F)}-{self.R(f'f:{short}_principal', y, F)}",
                  ctx.expect(y, P(line))))
        if line == "operating_lease":
            self.add(F, "f:rou_open", "Opening right-of-use asset", self.proj,
                     lambda y: (self.R("f:operating_rou", self.prev(y), F),
                                ctx.expect(y, lambda n, yy: P("operating_rou")(n,
                                                                               self.prev(yy)))))
            self.add(F, "f:operating_rou", "Closing right-of-use asset (opening - principal)",
                     self.all_years,
                     lambda y: (self.R("r:operating_lease_rou_asset", y, F),
                                ctx.expect(y, P("operating_rou"))) if y == self.origin else
                     (f"{self.R('f:rou_open', y, F)}-{self.R('f:ol_principal', y, F)}",
                      ctx.expect(y, P("operating_rou"))))
        self.gap_row(F)

    def plan_equity(self) -> None:
        ctx, F, P = self.ctx, FORECAST, self.ctx.projected  # noqa: N806
        self.heading(F, "Equity")
        self.add(F, "f:re_open", "Opening retained earnings", self.proj,
                 lambda y: (self.R("f:retained_earnings", self.prev(y), F),
                            ctx.expect(y, lambda n, yy: P("retained_earnings")(n,
                                                                               self.prev(yy)))))
        if ctx.payout:
            self.add(F, "f:dividends", "Dividends (payout x max(net income, 0))", self.proj,
                     lambda y: (f"{self.D('dividend_payout_ratio', F)}"
                                f"*MAX({self.R('m:net_income', y, F)},0)",
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy, "dividends"))))
        div = (lambda y: f"-{self.R('f:dividends', y, F)}") if ctx.payout else (lambda y: "")
        self.add(F, "f:retained_earnings", "Closing retained earnings", self.all_years,
                 lambda y: (self.R("r:retained_earnings", y, F),
                            ctx.expect(y, P("retained_earnings"))) if y == self.origin else
                 (f"{self.R('f:re_open', y, F)}+{self.R('m:net_income', y, F)}{div(y)}",
                  ctx.expect(y, P("retained_earnings"))))
        self.add(F, "f:oe_open", "Opening other equity", self.proj,
                 lambda y: (self.R("f:other_equity", self.prev(y), F),
                            ctx.expect(y, lambda n, yy: P("other_equity")(n, self.prev(yy)))))
        if ctx.repurchases:
            self.add(F, "f:repurchases", "Repurchases (three-year median)", self.proj,
                     lambda y: (self.D("repurchases", F),
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy, "repurchases"))))
        rep = (lambda y: f"-{self.R('f:repurchases', y, F)}") if ctx.repurchases \
            else (lambda y: "")
        self.add(F, "f:other_equity", "Closing other equity (equity - retained earnings at FY0)",
                 self.all_years,
                 lambda y: (self._other_equity_opening(y), ctx.expect(y, P("other_equity")))
                 if y == self.origin else
                 (f"{self.R('f:oe_open', y, F)}{rep(y)}" if ctx.repurchases
                  else self.R("f:oe_open", y, F), ctx.expect(y, P("other_equity"))))
        self.gap_row(F)

    def _other_equity_opening(self, y: int) -> str:
        """Equity (the I9 equity operands) - retained earnings. I9 subtracts equity, so each
        operand enters with the opposite of its sign in the identity."""
        text = "".join(f"{'+' if t.startswith('-') else '-'}{self.R('r:' + t[1:], y, FORECAST)}"
                       for t in self.ctx.equity_terms)
        text = text[1:] if text.startswith("+") else text
        return f"({text})-{self.R('r:retained_earnings', y, FORECAST)}"

    def plan_liquidity(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        sti = ctx.has("short_term_investments")
        self.heading(F, "Liquidity under the selected scenario (8.12; F5)")
        self.add(F, "f:cash", "Projected cash", self.proj,
                 lambda y: (self.R("m:cash", y, F),
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "cash"))))
        if sti:
            self.add(F, "f:sti", "Short-term investments (held constant)", self.proj,
                     lambda y: (self.R("m:short_term_investments", y, F),
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy,
                                                                     "short_term_investments"))))
        self.add(F, "f:liquidity", "Liquidity (cash + short-term investments)", self.proj,
                 lambda y: ((f"{self.R('f:cash', y, F)}+{self.R('f:sti', y, F)}" if sti
                             else self.R("f:cash", y, F)),
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "liquidity"))))
        if sti:
            self.add(F, "f:reserve", "Conditional securities reserve (securities x (1 - "
                                     "haircut); never counted as cash)", self.proj,
                     lambda y: (f"{self.R('f:sti', y, F)}*(1-{self.D('securities_haircut', F)})",
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy,
                                                                     "short_term_investments")
                                           * (1 - _spec(ctx, n).securities_haircut))))
        self.add(F, "f:floor", "Minimum cash floor", self.proj,
                 lambda y: (self.D("minimum_cash_floor", F),
                            ctx.expect(y, lambda n, yy: float(_spec(ctx, n).minimum_cash_floor))))
        self.add(F, "f:headroom", "Cash headroom (cash + reserve - floor)", self.proj,
                 lambda y: (f"{self.R('f:cash', y, F)}"
                            + (f"+{self.R('f:reserve', y, F)}" if sti else "")
                            + f"-{self.R('f:floor', y, F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "liquidity_headroom"))))
        self.add(F, "f:shortfall", "Funding shortfall (MODELLED CASH SHORTFALL if > 0; "
                                   "conditional on the scenario)", self.proj,
                 lambda y: (f"MAX(0,-{self.R('f:headroom', y, F)})",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "liquidity_shortfall"))))
        self.gap_row(F)

    # ---------------------------------------------------------------- statements
    def plan_income_statement(self) -> None:
        ctx, S = self.ctx, IS  # noqa: N806
        L = lambda line: (lambda n, yy: ctx.line(n, yy, line))  # noqa: E731,N806
        self.gap_row(S)
        self.heading(S, "Model (integrated forecast, blueprint 8.10): FY0 opening, "
                        "FY+1..FY+3 formulas driven by the Forecast sheet")
        rev = lambda y: (self.R("r:revenue", y, S), {ALL: ctx.last_revenue}) \
            if y == self.origin else \
            (f"{self.R('m:revenue', self.prev(y), S)}*(1+{self.D('revenue_growth', S)})",
             ctx.expect(y, L("revenue")))  # noqa: E731
        self.add(S, "m:revenue", "Revenue", self.all_years, rev)
        costs: list[str] = []
        if ctx.no_cogs:
            self.add(S, "m:cash_operating_costs", "Cash operating costs (NO_COGS: one total)",
                     self.proj, lambda y: (f"{self.R('m:revenue', y, S)}"
                                           f"*{self.D('cash_operating_cost_share', S)}",
                                           ctx.expect(y, L("cash_operating_costs"))))
            costs = ["m:cash_operating_costs"]
        else:
            self.add(S, "m:cogs", "Cost of revenue", self.proj,
                     lambda y: (f"{self.R('m:revenue', y, S)}*{self.D('cogs_share', S)}",
                                ctx.expect(y, L("cogs"))))
            self.add(S, "m:gross_profit", "Gross profit (model)", self.proj,
                     lambda y: (f"{self.R('m:revenue', y, S)}-{self.R('m:cogs', y, S)}",
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy, "revenue")
                                           - ctx.line(n, yy, "cogs"))))
            self.add(S, "m:other_cash_costs", "Other cash operating costs", self.proj,
                     lambda y: (f"{self.R('m:revenue', y, S)}"
                                f"*{self.D('other_cash_cost_share', S)}",
                                ctx.expect(y, L("other_cash_costs"))))
            costs = ["m:cogs", "m:other_cash_costs"]
        self.add(S, "m:depreciation", "Depreciation", self.proj,
                 lambda y: (self.R("f:depreciation", y, S), ctx.expect(y, L("depreciation"))))
        if ctx.has("operating_lease"):
            self.add(S, "m:operating_lease_cost", "Operating lease cost (= scheduled payment)",
                     self.proj, lambda y: (self.R("f:ol_payment", y, S),
                                           ctx.expect(y, L("operating_lease_cost"))))

        def ebit(y: int) -> tuple[str, Expected]:
            cost = "+".join(self.R(c, y, S) for c in costs)
            text = (f"{self.R('m:revenue', y, S)}-({cost})-{self.R('m:depreciation', y, S)}")
            if ctx.has("operating_lease"):
                text += f"-{self.R('m:operating_lease_cost', y, S)}"
            return text, ctx.expect(y, L("ebit"))
        self.add(S, "m:ebit", "EBIT", self.proj, ebit)
        interest_parts = []
        if ctx.has("debt"):
            self.add(S, "m:debt_interest", "Interest on debt", self.proj,
                     lambda y: (self.R("f:debt_interest", y, S), ctx.expect(y, L("debt_interest"))))
            interest_parts.append("m:debt_interest")
        if ctx.has("finance_lease"):
            self.add(S, "m:finance_lease_interest", "Interest on finance leases", self.proj,
                     lambda y: (self.R("f:fl_interest", y, S),
                                ctx.expect(y, L("finance_lease_interest"))))
            interest_parts.append("m:finance_lease_interest")
        if interest_parts:
            self.add(S, "m:interest", "Interest expense", self.proj,
                     lambda y: ("+".join(self.R(k, y, S) for k in interest_parts),
                                ctx.expect(y, L("interest"))))
        self.add(S, "m:pretax", "Pre-tax income", self.proj,
                 lambda y: (f"{self.R('m:ebit', y, S)}-{self.R('m:interest', y, S)}"
                            if interest_parts else self.R("m:ebit", y, S),
                            ctx.expect(y, L("pretax"))))
        self.add(S, "m:tax", "Income tax (no benefit booked on a loss)", self.proj,
                 lambda y: (f"IF({self.R('m:pretax', y, S)}>0,{self.R('m:pretax', y, S)}"
                            f"*{self.D('tax_rate', S)},0)", ctx.expect(y, L("tax"))))
        self.add(S, "m:net_income", "Net income", self.proj,
                 lambda y: (f"{self.R('m:pretax', y, S)}-{self.R('m:tax', y, S)}",
                            ctx.expect(y, L("net_income"))))

    def plan_balance_sheet(self) -> None:
        ctx, S, P = self.ctx, BS, self.ctx.projected  # noqa: N806
        self.gap_row(S)
        self.heading(S, "Model (integrated forecast, blueprint 8.10): FY0 opening position, "
                        "FY+1..FY+3 formulas; no plug")
        every = self.all_years
        assets, liabilities, equity = [], [], []

        def link(key: str, line: str, label: str, side: list[str], src: str) -> None:
            self.add(S, key, label, every, lambda y: (self.R(src, y, S), ctx.expect(y, P(line))))
            side.append(key)

        self.add(S, "m:cash", "Cash and cash equivalents (projected: cash flow closing cash)",
                 every, lambda y: (self.R("r:cash_and_cash_equivalents", y, S),
                                   ctx.expect(y, P("cash"))) if y == self.origin else
                 (self.R("m:cf_closing_cash", y, S), ctx.expect(y, P("cash"))))
        assets.append("m:cash")
        if ctx.has("short_term_investments"):
            self.add(S, "m:short_term_investments", "Short-term investments (held constant)",
                     every, lambda y: (self.R("r:short_term_investments", y, S),
                                       ctx.expect(y, P("short_term_investments")))
                     if y == self.origin else
                     (self.R("m:short_term_investments", self.prev(y), S),
                      ctx.expect(y, P("short_term_investments"))))
            assets.append("m:short_term_investments")
        for line in ("receivables", "inventory"):
            if ctx.has(line):
                link(f"m:{line}", line, line.capitalize(), assets, f"f:{line}")
        link("m:ppe", "ppe", "PP&E, net", assets, "f:ppe")
        if ctx.has("operating_rou"):
            link("m:operating_rou", "operating_rou", "Operating lease right-of-use asset",
                 assets, "f:operating_rou")
        listed_assets = list(assets)
        self.add(S, "m:other_assets", "Other assets (total assets - listed assets at FY0; held "
                                      "constant)", every,
                 lambda y: (f"{self.R('r:total_assets', y, S)}-("
                            + "+".join(self.R(k, y, S) for k in listed_assets) + ")",
                            ctx.expect(y, P("other_assets"))) if y == self.origin else
                 (self.R("m:other_assets", self.prev(y), S), ctx.expect(y, P("other_assets"))))
        assets.append("m:other_assets")
        self.add(S, "m:total_assets", "Total assets", every,
                 lambda y: ("+".join(self.R(k, y, S) for k in assets),
                            ctx.expect(y, self._total(("cash", "short_term_investments",
                                                       "receivables", "inventory", "ppe",
                                                       "operating_rou", "other_assets")))))
        if ctx.has("payables"):
            link("m:payables", "payables", "Accounts payable", liabilities, "f:payables")
        if ctx.has("debt"):
            link("m:debt", "debt", "Funded debt", liabilities, "f:debt")
        if ctx.has("operating_lease"):
            link("m:operating_lease", "operating_lease", "Operating lease liability",
                 liabilities, "f:operating_lease")
        if ctx.has("finance_lease"):
            link("m:finance_lease", "finance_lease", "Finance lease liability", liabilities,
                 "f:finance_lease")
        listed = list(liabilities)
        self.add(S, "m:other_liabilities", "Other liabilities (total liabilities - listed at FY0; "
                                           "held constant)", every,
                 lambda y: (f"{self.R('r:total_liabilities', y, S)}"
                            + (("-(" + "+".join(self.R(k, y, S) for k in listed) + ")")
                               if listed else ""),
                            ctx.expect(y, P("other_liabilities"))) if y == self.origin else
                 (self.R("m:other_liabilities", self.prev(y), S),
                  ctx.expect(y, P("other_liabilities"))))
        liabilities.append("m:other_liabilities")
        self.add(S, "m:total_liabilities", "Total liabilities", every,
                 lambda y: ("+".join(self.R(k, y, S) for k in liabilities),
                            ctx.expect(y, self._total(("payables", "debt", "operating_lease",
                                                       "finance_lease", "other_liabilities")))))
        link("m:retained_earnings", "retained_earnings", "Retained earnings", equity,
             "f:retained_earnings")
        link("m:other_equity", "other_equity", "Other equity", equity, "f:other_equity")
        self.add(S, "m:total_equity", "Total equity", every,
                 lambda y: ("+".join(self.R(k, y, S) for k in equity),
                            ctx.expect(y, self._total(("retained_earnings", "other_equity")))))
        self.add(S, "chk:F1", "Balance check F1: assets - liabilities - equity (each line "
                              "summed afresh)", every,
                 lambda y: ("(" + "+".join(self.R(k, y, S) for k in assets) + ")-("
                            + "+".join(self.R(k, y, S) for k in liabilities) + ")-("
                            + "+".join(self.R(k, y, S) for k in equity) + ")",
                            ctx.expect(y, lambda n, yy: 0.0)))
        self.add(S, "chk:F1_status", "F1 status", every, self._status("chk:F1", S), "text",
                 "exact")

    def _total(self, lines: Sequence[str]) -> Callable[[str, int], float]:
        def get(n: str, y: int) -> float:
            if y == self.origin:
                return sum(self.ctx.opening[l] for l in lines if l in self.ctx.opening)
            values = self.ctx.forecasts[n].years[y - self.origin - 1].lines
            return sum(values[l] for l in lines if l in values)
        return get

    def _status(self, key: str, sheet: str, exact: bool = False):
        tol = lambda: self.D("tolerance", sheet)  # noqa: E731
        return lambda y: ((f'IF({self.R(key, y, sheet)}=0,"OK","FAIL")' if exact else
                           f'IF(ABS({self.R(key, y, sheet)})<={tol()},"OK","FAIL")'),
                          self.ctx.expect(y, lambda n, yy: "OK"))

    def plan_cash_flow(self) -> None:
        ctx, S = self.ctx, CF  # noqa: N806
        L = lambda line, sign=1.0: (lambda n, yy: sign * ctx.line(n, yy, line))  # noqa: E731,N806
        self.gap_row(S)
        self.heading(S, "Model (integrated forecast, blueprint 8.10): cash is the result of the "
                        "cash flow statement")
        p = self.proj
        self.add(S, "m:cf_net_income", "Net income", p,
                 lambda y: (self.R("m:net_income", y, S), ctx.expect(y, L("net_income"))))
        self.add(S, "m:cf_depreciation", "Depreciation", p,
                 lambda y: (self.R("m:depreciation", y, S), ctx.expect(y, L("depreciation"))))
        self.add(S, "m:cf_wc", "Working-capital cash flow", p,
                 lambda y: (self.R("f:wc_cf", y, S), ctx.expect(y, L("working_capital_cf"))))
        self.add(S, "m:cfo", "Operating cash flow", p,
                 lambda y: (f"{self.R('m:cf_net_income', y, S)}+{self.R('m:cf_depreciation', y, S)}"
                            f"+{self.R('m:cf_wc', y, S)}", ctx.expect(y, L("cfo"))))
        self.add(S, "m:cf_capex", "Capital expenditure", p,
                 lambda y: (f"-{self.R('f:capex', y, S)}", ctx.expect(y, L("capex", -1.0))))
        self.add(S, "m:cfi", "Investing cash flow", p,
                 lambda y: (self.R("m:cf_capex", y, S), ctx.expect(y, L("cfi"))))
        financing = []
        if ctx.has("debt"):
            self.add(S, "m:cf_new_debt", "New debt", p,
                     lambda y: (self.R("f:new_debt", y, S), ctx.expect(y, L("new_debt"))))
            self.add(S, "m:cf_debt_principal", "Debt principal repaid", p,
                     lambda y: (f"-{self.R('f:debt_principal', y, S)}",
                                ctx.expect(y, L("debt_principal", -1.0))))
            financing += ["m:cf_new_debt", "m:cf_debt_principal"]
        if ctx.has("finance_lease"):
            self.add(S, "m:cf_fl_principal", "Finance lease principal repaid", p,
                     lambda y: (f"-{self.R('f:fl_principal', y, S)}",
                                ctx.expect(y, L("finance_lease_principal", -1.0))))
            financing.append("m:cf_fl_principal")
        if ctx.payout:
            self.add(S, "m:cf_dividends", "Dividends paid", p,
                     lambda y: (f"-{self.R('f:dividends', y, S)}",
                                ctx.expect(y, L("dividends", -1.0))))
            financing.append("m:cf_dividends")
        if ctx.repurchases:
            self.add(S, "m:cf_repurchases", "Repurchases", p,
                     lambda y: (f"-{self.R('f:repurchases', y, S)}",
                                ctx.expect(y, L("repurchases", -1.0))))
            financing.append("m:cf_repurchases")
        self.add(S, "m:cff", "Financing cash flow", p,
                 lambda y: ("+".join(self.R(k, y, S) for k in financing) if financing else "0",
                            ctx.expect(y, L("cff"))))
        self.add(S, "m:cf_opening_cash", "Opening cash", p,
                 lambda y: (self.R("m:cash", self.prev(y), S),
                            ctx.expect(y, lambda n, yy: ctx.projected("cash")(n, self.prev(yy)))))
        self.add(S, "m:cf_closing_cash", "Closing cash (opening + CFO + CFI + CFF)", p,
                 lambda y: (f"{self.R('m:cf_opening_cash', y, S)}+{self.R('m:cfo', y, S)}"
                            f"+{self.R('m:cfi', y, S)}+{self.R('m:cff', y, S)}",
                            ctx.expect(y, L("cash"))))
        self.add(S, "chk:cash_tieout", "Cash tie-out: balance sheet cash - closing cash", p,
                 lambda y: (f"{self.R('m:cash', y, S)}-{self.R('m:cf_closing_cash', y, S)}",
                            ctx.expect(y, lambda n, yy: 0.0)))
        self.add(S, "chk:cash_tieout_status", "Cash tie-out status", p,
                 self._status("chk:cash_tieout", S), "text", "exact")

    # ---------------------------------------------------------------- F2-F5
    def plan_checks(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        p = self.proj
        self.heading(F, "Model checks (Appendix B), recomputed independently of the lines above")

        def f2(y: int) -> tuple[str, Expected]:
            R = lambda k, yy=y: self.R(k, yy, F)  # noqa: E731,N806
            terms = [R("m:cash", self.prev(y)), f"+{R('m:ebit')}", f"+{R('m:depreciation')}",
                     f"+{R('f:wc_cf')}", f"-{R('m:tax')}"]
            if self.g.has("m:interest", y):
                terms.append(f"-{R('m:interest')}")
            terms.append(f"-{R('f:capex')}")
            if ctx.has("debt"):
                terms += [f"-{R('f:debt_principal')}", f"+{R('f:new_debt')}"]
            if ctx.has("finance_lease"):
                terms.append(f"-{R('f:fl_principal')}")
            if ctx.payout:
                terms.append(f"-{R('f:dividends')}")
            if ctx.repurchases:
                terms.append(f"-{R('f:repurchases')}")
            return f"{R('m:cash')}-(" + "".join(terms) + ")", ctx.expect(y, lambda n, yy: 0.0)

        self.add(F, "chk:F2", "F2 closing cash - (opening cash + EBIT + depreciation + WC - "
                              "tax - interest - capex - principal - dividends + new debt - "
                              "repurchases)", p, f2)
        self.add(F, "chk:F2_status", "F2 status", p, self._status("chk:F2", F), "text", "exact")

        def f3(y: int) -> tuple[str, Expected]:
            prev = self.prev(y)
            parts = []
            for line, sign in (("receivables", "-"), ("inventory", "-"), ("payables", "+")):
                if ctx.has(line):
                    parts.append(f"{sign}({self.R(f'f:{line}', y, F)}"
                                 f"-{self.R(f'f:{line}', prev, F)})")
            inner = "".join(parts) or "0"
            return (f"{self.R('f:wc_cf', y, F)}-({inner})", ctx.expect(y, lambda n, yy: 0.0))

        self.add(F, "chk:F3", "F3 working-capital cash flow - (-(change in receivables + "
                              "inventory - payables))", p, f3)
        self.add(F, "chk:F3_status", "F3 status", p, self._status("chk:F3", F), "text", "exact")
        pairs = [(k, o) for k, o, line in (("f:debt_principal", "f:debt_open", "debt"),
                                           ("f:ol_principal", "f:ol_open", "operating_lease"),
                                           ("f:fl_principal", "f:fl_open", "finance_lease"))
                 if ctx.has(line)]
        if pairs:
            self.add(F, "chk:F4", "F4 largest breach of 0 <= principal <= opening balance", p,
                     lambda y: ("MAX(0," + ",".join(
                         f"-{self.R(k, y, F)},{self.R(k, y, F)}-{self.R(o, y, F)}"
                         for k, o in pairs) + ")", ctx.expect(y, lambda n, yy: 0.0)), "dollars",
                     "exact")
            self.add(F, "chk:F4_status", "F4 status (exact)", p,
                     self._status("chk:F4", F, exact=True), "text", "exact")
        if ctx.liquidity_known:
            def f5(y: int) -> tuple[str, Expected]:
                R = lambda k: self.R(k, y, F)  # noqa: E731,N806
                h = self.D("securities_haircut", F)
                fl = self.D("minimum_cash_floor", F)
                again = f"({R('m:cash')}" + (f"+{R('m:short_term_investments')}*(1-{h})"
                                              if ctx.has("short_term_investments") else "") \
                    + f"-{fl})"
                return (f"MAX(ABS({R('f:headroom')}-{again}),"
                        f"ABS({R('f:shortfall')}-MAX(0,-{again})))",
                        ctx.expect(y, lambda n, yy: 0.0))
            self.add(F, "chk:F5", "F5 headroom and shortfall recomputed from cash, securities, "
                                  "haircut and floor", p, f5)
            self.add(F, "chk:F5_status", "F5 status", p, self._status("chk:F5", F), "text",
                     "exact")


def _col(n: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(n)


def _spec(ctx: ModelContext, name: str) -> ScenarioSpec:
    return next(s for s in ctx.specs if s.name == name)


def banner(ws: Worksheet, row: int, text: str, width: int) -> None:
    for c in range(1, width + 1):
        ws.cell(row=row, column=c).fill = BANNER_FILL
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = BOLD
