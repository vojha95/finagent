"""The integrated model as live formulas: the Forecast sheet and the projected columns of the
statement sheets (blueprint 8.10, 8.11; Phase 4d; BC21).

The Forecast sheet is laid out in titled sections: scenario (selector, status, all checks), the
scenario table of configs/scenarios.yaml, switches, the MODEL_ASSUMPTION drivers (base value in
blue, scenario value as a formula), the 10-K maturity schedules (blue REPORTED_FACT inputs in
the projected year columns), the schedules (working capital, PP&E, debt, leases, equity), the
liquidity block (F5) and the model checks F1-F5. Per-year rows use the statement sheets' year
columns. The projected statement lines are written onto the rows of configs/workbook.yaml: FY0
is the historical cell of that row (the opening position), FY+1..FY+3 are formulas only. The
formulas mirror finagent.model.forecast.project line by line; every flow is posted twice and
cash is the result of the cash flow, so the balance sheet balances without a plug.

Every formula cell carries the Python model's value per scenario as its expected value.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from finagent.config import ForecastConfig, ScenarioSpec
from finagent.model.forecast import (ASSET_LINES, EQUITY_LINES, LIABILITY_LINES,
                                     SHORT_TERM_INVESTMENTS_IN_OTHER_ASSETS, VERIFIED)
from finagent.model.shocks import shock_drivers
from finagent.records import Forecast, ModelAssumption
from finagent.workbook.grid import (ALL, BANNER_FILL, BOLD, GREY, INPUT_FILL, Columns,
                                    Expected, Grid, section, year_header)

FORECAST = "Forecast"
IS, BS, CF = "Income Statement", "Balance Sheet", "Cash Flow"
BASE_COL, SCENARIO_COL, STATUS_COL, NOTE_COL = 2, 3, 4, 5  # Forecast input columns

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


LineBuild = Callable[[int], tuple[str, Expected]]


@dataclass
class Line:
    sheet: str
    key: str
    row: int
    years: list[int]
    build: LineBuild
    label: str
    fmt: str
    tol: str


class ModelWriter:
    """Writes the Forecast sheet, and the projected cells of the statement sheets onto the rows
    of configs/workbook.yaml (model_rows: (sheet, model key) -> (sheet row, template row id)).

    With model_rows None the writer only collects the statement keys it would write
    (statement_keys)."""

    def __init__(self, grid: Grid, ctx: ModelContext, sheets: Mapping[str, Worksheet],
                 model_rows: Mapping[tuple[str, str], tuple[int, str]] | None) -> None:
        self.g, self.ctx, self.ws = grid, ctx, sheets
        self.model_rows = model_rows
        self.row = {FORECAST: 1}
        self.origin = ctx.origin
        self.proj = grid.cols.proj
        self.lines: list[Line] = []
        self.status_cells: list[str] = []
        self.shocked_cells: list[str] = []
        self.scenario_status_row = 0
        self.all_checks_row = 0

    # ---------------------------------------------------------------- planning helpers
    def add(self, sheet: str, key: str, label: str, years: Sequence[int], build: LineBuild,
            fmt: str = "dollars", tol: str = "dollar") -> None:
        if sheet == FORECAST:
            r = self.row[sheet]
            self.row[sheet] += 1
        elif self.model_rows is None:
            r = 0
        else:
            if (sheet, key) not in self.model_rows:
                raise KeyError(f"{sheet}: model line {key} has no row in configs/workbook.yaml")
            r, row_id = self.model_rows[(sheet, key)]
            for y in years:
                self.g.register(f"row:{sheet}:{row_id}", y, sheet,
                                f"{self.g.cols.letter(y)}{r}")
        if r:
            for y in years:
                self.g.register(key, y, sheet, f"{self.g.cols.letter(y)}{r}")
        self.lines.append(Line(sheet, key, r, list(years), build, label, fmt, tol))

    def gap_row(self, sheet: str, n: int = 1) -> None:
        self.row[sheet] += n

    def heading(self, sheet: str, text: str) -> None:
        """A bold sub-heading inside a Forecast section."""
        if sheet == FORECAST:
            self.ws[sheet].cell(row=self.row[sheet], column=1, value=text).font = BOLD
            self.row[sheet] += 1

    def block(self, text: str, years: Sequence[int]) -> None:
        """A titled Forecast section with its year header (the statement columns)."""
        ws, r = self.ws[FORECAST], self.row[FORECAST]
        section(ws, r, text, self.g.cols.note)
        year_header(ws, r + 1, self.g.cols, years)
        self.row[FORECAST] = r + 2

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
        for line in self.lines:
            ws = self.ws[line.sheet]
            if line.sheet == FORECAST:
                ws.cell(row=line.row, column=1, value=line.label)
            for y in line.years:
                formula, expected = line.build(y)
                self.g.formula(ws, line.row, self.g.cols.col(y), formula, expected,
                               fmt=line.fmt, tolerance=line.tol)
                if line.key.endswith("_status"):
                    self.status_cells.append(self.g.ref(line.key, y, FORECAST))

    # ---------------------------------------------------------------- Forecast: inputs
    def forecast_inputs(self, top: int) -> int:
        """Scenario block, scenario table, switches, drivers and maturity schedules; returns the
        next free row. Registers d:* and s:* keys."""
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        names = [s.name for s in ctx.specs]
        width = g.cols.note
        r = top
        section(ws, r, "Scenario", width)
        r += 1
        ws.cell(row=r, column=1, value="Scenario selector (Base / Moderate / Severe)").font = BOLD
        sel = g.value(ws, r, BASE_COL, ctx.base.scenario, key="d:selector", fmt="text")
        ws[sel].fill = INPUT_FILL
        dv = DataValidation(type="list", formula1='"' + ",".join(names) + '"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(sel)
        ws.cell(row=r, column=BASE_COL + 1, value="Change this cell: every shocked driver, all "
                                                  "three statements and the checks "
                                                  "recalculate.").font = GREY
        r += 1
        ws.cell(row=r, column=1, value="Scenario status")
        self.scenario_status_row = r
        r += 1
        ws.cell(row=r, column=1, value="All model checks (F1-F5, cash tie-out)")
        self.all_checks_row = r
        r += 2
        section(ws, r, "Scenario table (configs/scenarios.yaml; MODEL_ASSUMPTION, blueprint 8.11)",
                width)
        r += 1
        ws.cell(row=r, column=1, value="Shock").font = BOLD
        for i, n in enumerate(names):
            ws.cell(row=r, column=BASE_COL + i, value=n).font = BOLD
        sel_col = BASE_COL + len(names)
        ws.cell(row=r, column=sel_col, value="Selected").font = BOLD
        header = r
        r += 1
        first_name = f"${_col(BASE_COL)}${header}"
        last_name = f"${_col(sel_col - 1)}${header}"
        for field, label, fmt in SHOCKS:
            ws.cell(row=r, column=1, value=label)
            for i, spec in enumerate(ctx.specs):
                g.value(ws, r, BASE_COL + i, float(getattr(spec, field)), fmt=fmt)
            rng = f"${_col(BASE_COL)}${r}:${_col(sel_col - 1)}${r}"
            g.formula(ws, r, sel_col,
                      f"INDEX({rng},MATCH({g.absolute('d:selector', None, FORECAST)},"
                      f"{first_name}:{last_name},0))",
                      {s.name: float(getattr(s, field)) for s in ctx.specs},
                      key=f"d:shock:{field}", fmt=fmt, tolerance="ratio")
            r += 1
        ws.cell(row=r, column=1, value="Days in year (days shocks -> shares)")
        g.value(ws, r, BASE_COL, float(ctx.specs[0].days_in_year), key="d:days", fmt="days")
        r += 2
        section(ws, r, "Switches and tolerance", width)
        r += 1
        ws.cell(row=r, column=1, value="Refinance maturities (1 = new debt equals scheduled "
                                       "principal; 0 = no new debt)")
        g.value(ws, r, BASE_COL, 1.0 if ctx.config.refinance_maturities else 0.0,
                key="d:refinance", fmt="days")
        r += 1
        ws.cell(row=r, column=1, value="Working-capital basis (driver values below use it)")
        ws.cell(row=r, column=BASE_COL, value=str(ctx.base.switches.get("working_capital_basis")))
        r += 1
        ws.cell(row=r, column=1, value="Check tolerance ($; Appendix B F1-F3, F5)")
        g.value(ws, r, BASE_COL, float(ctx.config.tolerance), key="d:tolerance", fmt="days")
        r += 2
        r = self.drivers_table(r)
        r = self.schedule_inputs(r + 1)
        return r + 1

    def drivers_table(self, r: int) -> int:
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        section(ws, r, "Drivers (MODEL_ASSUMPTION, blueprint 8.10): base value in blue; "
                       "scenario value = base + selected shock", g.cols.note)
        r += 1
        for c, text in ((1, "Driver"), (BASE_COL, "Base"), (SCENARIO_COL, "Scenario"),
                        (STATUS_COL, "Status"), (NOTE_COL, "Basis, flags and note")):
            ws.cell(row=r, column=c, value=text).font = BOLD
        r += 1
        for a in ctx.base.assumptions:
            ws.cell(row=r, column=1, value=a.driver_id)
            fmt = "rate" if a.driver_id in RATE_FORMAT else "dollars"
            if a.driver_id in SCHEDULE_DRIVERS:
                fmt = "dollars"
            if usable_driver(a):
                base = g.value(ws, r, BASE_COL, float(a.value), key=f"d:base:{a.driver_id}",
                               fmt=fmt)
                self._scenario_value(ws, r, a, base, fmt)
            else:
                g.empty(ws, r, BASE_COL, f"{a.status}: {a.detail}")
            ws.cell(row=r, column=STATUS_COL, value=a.status)
            note = a.detail
            if a.raw_value is not None and a.raw_value != a.value:
                note = f"raw {a.raw_value:.6g}; {note}"
            if a.inputs:
                note += "; inputs: " + ", ".join(f"{i.metric} FY{i.fiscal_year} {i.status}"
                                                 for i in a.inputs[:6])
            text = "; ".join(x for x in (a.basis, ", ".join(a.flags), note) if x)
            ws.cell(row=r, column=NOTE_COL, value=text).font = GREY
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
        else:
            formula = base
        coord = g.formula(ws, r, SCENARIO_COL, formula, expected, key=f"d:{a.driver_id}",
                          fmt=fmt, tolerance="ratio" if fmt == "rate" else "dollar")
        if a.driver_id in DRIVER_SHOCKS and a.driver_id != "revenue_growth":
            self.shocked_cells.append(coord)

    def schedule_inputs(self, r: int) -> int:
        """The 10-K maturity schedules: blue REPORTED_FACT inputs in the projected year
        columns (year 1 of the schedule = FY+1)."""
        ws, g, ctx = self.ws[FORECAST], self.g, self.ctx
        section(ws, r, "Maturity schedules (10-K, FY0 filing; REPORTED_FACT inputs)", g.cols.note)
        year_header(ws, r + 1, g.cols, self.proj)
        r += 2
        for driver_id, label in SCHEDULE_DRIVERS.items():
            a = ctx.drivers.get(driver_id)
            if not usable_driver(a):
                continue
            assert a is not None
            ws.cell(row=r, column=1, value=label)
            for y, ref in zip(self.proj, a.inputs):
                g.value(ws, r, g.cols.col(y), float(ref.value), key=f"s:{driver_id}", year=y)
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
        g.formula(ws, self.scenario_status_row, BASE_COL, formula, expected, fmt="text",
                  tolerance="exact")

    def all_checks(self) -> None:
        ws, g = self.ws[FORECAST], self.g
        cells = ",".join(f'{c}="OK"' for c in self.status_cells)
        g.formula(ws, self.all_checks_row, BASE_COL,
                  f'IF(AND({cells}),"OK","FAIL")' if cells else '"OK"',
                  {n: "OK" for n in self.ctx.verified}, key="d:all_checks", fmt="text",
                  tolerance="exact")

    # ---------------------------------------------------------------- plan every model line
    def plan(self) -> None:
        self.plan_forecast_schedules()
        self.plan_income_statement()
        self.plan_balance_sheet()
        self.plan_cash_flow()
        if self.ctx.liquidity_known:
            self.plan_liquidity()
        self.plan_checks()

    def plan_forecast_schedules(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        P = ctx.projected  # noqa: N806
        proj, every = self.proj, self.all_years

        def opening(key: str, line: str) -> LineBuild:
            return lambda y: (self.R(key, self.prev(y), F),
                              ctx.expect(y, lambda n, yy: P(line)(n, self.prev(yy))))

        def rolled(key: str, line: str, origin_key: str, build: LineBuild) -> LineBuild:
            """FY0: the statement-sheet opening cell; FY+1..: the roll-forward."""
            return lambda y: (self.R(origin_key, y, F), ctx.expect(y, P(line))) \
                if y == self.origin else build(y)

        self.block("Schedules (FY0 = the balance-sheet opening position; projections are "
                   "formulas)", every)
        # Working capital
        self.heading(F, "Working capital (shares of revenue, or of COGS / revenue for NO_COGS)")
        base_key = "m:revenue" if ctx.no_cogs else "m:cogs"
        for line, driver, base in (("receivables", "receivables_share", "m:revenue"),
                                   ("inventory", "inventory_share", base_key),
                                   ("payables", "payables_share", base_key)):
            if not ctx.has(line):
                continue
            self.add(F, f"f:{line}", line.capitalize(), every, rolled(
                f"f:{line}", line, f"m:{line}",
                lambda y, base=base, driver=driver, line=line: (
                    f"{self.R(base, y, F)}*{self.D(driver, F)}", ctx.expect(y, P(line)))))
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
        self.add(F, "f:ppe", "Closing PP&E", every, rolled(
            "f:ppe", "ppe", "m:ppe",
            lambda y: (f"{self.R('f:ppe_open', y, F)}+{self.R('f:capex', y, F)}"
                       f"-{self.R('f:depreciation', y, F)}", ctx.expect(y, P("ppe")))))
        self.gap_row(F)
        if ctx.has("debt"):
            self.plan_debt(rolled)
        if ctx.has("finance_lease"):
            self.plan_lease("finance_lease", "fl", "Finance lease", "finance_lease_rate",
                            "finance_lease_payments", rolled)
        if ctx.has("operating_lease"):
            self.plan_lease("operating_lease", "ol", "Operating lease", "operating_lease_rate",
                            "operating_lease_payments", rolled)
        self.plan_equity(rolled)

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

    def plan_debt(self, rolled) -> None:
        ctx, F, P = self.ctx, FORECAST, self.ctx.projected  # noqa: N806
        self.heading(F, "Funded debt (scheduled principal from the 10-K maturity table)")
        self.add(F, "f:debt_open", "Opening debt", self.proj,
                 lambda y: (self.R("f:debt", self.prev(y), F),
                            ctx.expect(y, lambda n, yy: P("debt")(n, self.prev(yy)))))
        self.add(F, "f:debt_principal", "Scheduled principal", self.proj,
                 lambda y: (self.R("s:debt_principal", y, F),
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "debt_principal"))))
        self.add(F, "f:new_debt", "New debt (scheduled principal x refinance switch)", self.proj,
                 lambda y: (f"{self.R('f:debt_principal', y, F)}*{self.D('refinance', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "new_debt"))))
        self.add(F, "f:debt", "Closing debt", self.all_years, rolled(
            "f:debt", "debt", "m:debt",
            lambda y: (f"{self.R('f:debt_open', y, F)}-{self.R('f:debt_principal', y, F)}"
                       f"+{self.R('f:new_debt', y, F)}", ctx.expect(y, P("debt")))))
        self.add(F, "f:debt_interest", "Interest on debt (opening x scenario rate)", self.proj,
                 lambda y: (f"{self.R('f:debt_open', y, F)}*{self.D('interest_rate', F)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "debt_interest"))))
        self.gap_row(F)

    def plan_lease(self, line: str, short: str, title: str, rate: str, schedule: str,
                   rolled) -> None:
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
        self.add(F, f"f:{line}", f"Closing {title.lower()} liability", self.all_years, rolled(
            f"f:{line}", line, f"m:{line}",
            lambda y: (f"{self.R(f'f:{short}_open', y, F)}-{self.R(f'f:{short}_principal', y, F)}",
                       ctx.expect(y, P(line)))))
        if line == "operating_lease":
            self.add(F, "f:rou_open", "Opening right-of-use asset", self.proj,
                     lambda y: (self.R("f:operating_rou", self.prev(y), F),
                                ctx.expect(y, lambda n, yy: P("operating_rou")(n,
                                                                               self.prev(yy)))))
            self.add(F, "f:operating_rou", "Closing right-of-use asset (opening - principal)",
                     self.all_years, rolled(
                         "f:operating_rou", "operating_rou", "m:operating_rou",
                         lambda y: (f"{self.R('f:rou_open', y, F)}-{self.R('f:ol_principal', y, F)}",
                                    ctx.expect(y, P("operating_rou")))))
        self.gap_row(F)

    def plan_equity(self, rolled) -> None:
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
        self.add(F, "f:retained_earnings", "Closing retained earnings", self.all_years, rolled(
            "f:retained_earnings", "retained_earnings", "m:retained_earnings",
            lambda y: (f"{self.R('f:re_open', y, F)}+{self.R('m:net_income', y, F)}{div(y)}",
                       ctx.expect(y, P("retained_earnings")))))
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
                 self.all_years, rolled(
                     "f:other_equity", "other_equity", "m:other_equity",
                     lambda y: (f"{self.R('f:oe_open', y, F)}{rep(y)}",
                                ctx.expect(y, P("other_equity")))))
        self.gap_row(F)

    def plan_liquidity(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        sti = ctx.has("short_term_investments")
        self.block("Liquidity under the selected scenario (blueprint 8.12; F5)", self.proj)
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

    # ---------------------------------------------------------------- statements (FY+1..)
    def plan_income_statement(self) -> None:
        ctx, S, p = self.ctx, IS, self.proj  # noqa: N806
        L = lambda line: (lambda n, yy: ctx.line(n, yy, line))  # noqa: E731,N806
        self.add(S, "m:revenue", "Revenue", p,
                 lambda y: (f"{self.R('m:revenue', self.prev(y), S)}"
                            f"*(1+{self.D('revenue_growth', S)})", ctx.expect(y, L("revenue"))))
        if ctx.no_cogs:
            self.add(S, "m:cash_operating_costs", "Cash operating costs", p,
                     lambda y: (f"{self.R('m:revenue', y, S)}"
                                f"*{self.D('cash_operating_cost_share', S)}",
                                ctx.expect(y, L("cash_operating_costs"))))
            costs = ["m:cash_operating_costs"]
        else:
            self.add(S, "m:cogs", "Cost of revenue", p,
                     lambda y: (f"{self.R('m:revenue', y, S)}*{self.D('cogs_share', S)}",
                                ctx.expect(y, L("cogs"))))
            self.add(S, "m:gross_profit", "Gross profit", p,
                     lambda y: (f"{self.R('m:revenue', y, S)}-{self.R('m:cogs', y, S)}",
                                ctx.expect(y, lambda n, yy: ctx.line(n, yy, "revenue")
                                           - ctx.line(n, yy, "cogs"))))
            self.add(S, "m:other_cash_costs", "Other cash operating costs", p,
                     lambda y: (f"{self.R('m:revenue', y, S)}"
                                f"*{self.D('other_cash_cost_share', S)}",
                                ctx.expect(y, L("other_cash_costs"))))
            costs = ["m:cogs", "m:other_cash_costs"]
        self.add(S, "m:depreciation", "Depreciation", p,
                 lambda y: (self.R("f:depreciation", y, S), ctx.expect(y, L("depreciation"))))
        if ctx.has("operating_lease"):
            self.add(S, "m:operating_lease_cost", "Operating lease cost", p,
                     lambda y: (self.R("f:ol_payment", y, S),
                                ctx.expect(y, L("operating_lease_cost"))))

        def ebit(y: int) -> tuple[str, Expected]:
            cost = "+".join(self.R(c, y, S) for c in costs)
            text = f"{self.R('m:revenue', y, S)}-({cost})-{self.R('m:depreciation', y, S)}"
            if ctx.has("operating_lease"):
                text += f"-{self.R('m:operating_lease_cost', y, S)}"
            return text, ctx.expect(y, L("ebit"))
        self.add(S, "m:ebit", "EBIT", p, ebit)
        interest = [k for k, line in (("f:debt_interest", "debt"),
                                      ("f:fl_interest", "finance_lease")) if ctx.has(line)]
        if interest:
            self.add(S, "m:interest", "Interest expense", p,
                     lambda y: ("+".join(self.R(k, y, S) for k in interest),
                                ctx.expect(y, L("interest"))))
        self.add(S, "m:pretax", "Pre-tax income", p,
                 lambda y: (f"{self.R('m:ebit', y, S)}-{self.R('m:interest', y, S)}"
                            if interest else self.R("m:ebit", y, S), ctx.expect(y, L("pretax"))))
        self.add(S, "m:tax", "Income tax (no benefit booked on a loss)", p,
                 lambda y: (f"IF({self.R('m:pretax', y, S)}>0,{self.R('m:pretax', y, S)}"
                            f"*{self.D('tax_rate', S)},0)", ctx.expect(y, L("tax"))))
        self.add(S, "m:net_income", "Net income", p,
                 lambda y: (f"{self.R('m:pretax', y, S)}-{self.R('m:tax', y, S)}",
                            ctx.expect(y, L("net_income"))))

    def plan_balance_sheet(self) -> None:
        ctx, S, P, p = self.ctx, BS, self.ctx.projected, self.proj  # noqa: N806
        assets, liabilities, equity = [], [], []

        def link(key: str, line: str, side: list[str], src: str) -> None:
            self.add(S, key, line, p, lambda y: (self.R(src, y, S), ctx.expect(y, P(line))))
            side.append(key)

        def constant(key: str, line: str, side: list[str]) -> None:
            self.add(S, key, line, p, lambda y: (self.R(key, self.prev(y), S),
                                                 ctx.expect(y, P(line))))
            side.append(key)

        link("m:cash", "cash", assets, "m:cf_closing_cash")
        if ctx.has("short_term_investments"):
            constant("m:short_term_investments", "short_term_investments", assets)
        for line in ("receivables", "inventory"):
            if ctx.has(line):
                link(f"m:{line}", line, assets, f"f:{line}")
        link("m:ppe", "ppe", assets, "f:ppe")
        if ctx.has("operating_rou"):
            link("m:operating_rou", "operating_rou", assets, "f:operating_rou")
        constant("m:other_assets", "other_assets", assets)
        self.add(S, "m:total_assets", "Total assets", p,
                 lambda y: ("+".join(self.R(k, y, S) for k in assets),
                            ctx.expect(y, self._total(ASSET_LINES))))
        if ctx.has("payables"):
            link("m:payables", "payables", liabilities, "f:payables")
        for line in ("debt", "operating_lease", "finance_lease"):
            if ctx.has(line):
                link(f"m:{line}", line, liabilities, f"f:{line}")
        constant("m:other_liabilities", "other_liabilities", liabilities)
        self.add(S, "m:total_liabilities", "Total liabilities", p,
                 lambda y: ("+".join(self.R(k, y, S) for k in liabilities),
                            ctx.expect(y, self._total(LIABILITY_LINES))))
        link("m:retained_earnings", "retained_earnings", equity, "f:retained_earnings")
        link("m:other_equity", "other_equity", equity, "f:other_equity")
        self.add(S, "m:total_equity", "Total equity", p,
                 lambda y: ("+".join(self.R(k, y, S) for k in equity),
                            ctx.expect(y, self._total(EQUITY_LINES))))
        self.add(S, "m:total_le", "Total liabilities and equity", p,
                 lambda y: (f"{self.R('m:total_liabilities', y, S)}"
                            f"+{self.R('m:total_equity', y, S)}",
                            ctx.expect(y, self._total(LIABILITY_LINES + EQUITY_LINES))))
        self.add(S, "chk:F1", "Balance check F1: assets - liabilities - equity (each line "
                              "summed afresh)", p,
                 lambda y: ("(" + "+".join(self.R(k, y, S) for k in assets) + ")-("
                            + "+".join(self.R(k, y, S) for k in liabilities) + ")-("
                            + "+".join(self.R(k, y, S) for k in equity) + ")",
                            ctx.expect(y, lambda n, yy: 0.0)), "check")

    def _total(self, lines: Sequence[str]) -> Callable[[str, int], float]:
        def get(n: str, y: int) -> float:
            if y == self.origin:
                return sum(self.ctx.opening[l] for l in lines if l in self.ctx.opening)
            values = self.ctx.forecasts[n].years[y - self.origin - 1].lines
            return sum(values[l] for l in lines if l in values)
        return get

    def _status(self, key: str, exact: bool = False) -> LineBuild:
        F = FORECAST  # noqa: N806
        return lambda y: ((f'IF({self.R(key, y, F)}=0,"OK","FAIL")' if exact else
                           f'IF(ABS({self.R(key, y, F)})<={self.D("tolerance", F)},"OK","FAIL")'),
                          self.ctx.expect(y, lambda n, yy: "OK"))

    def plan_cash_flow(self) -> None:
        ctx, S, p = self.ctx, CF, self.proj  # noqa: N806
        L = lambda line, sign=1.0: (lambda n, yy: sign * ctx.line(n, yy, line))  # noqa: E731,N806
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
            self.add(S, "m:cf_new_debt", "Debt issued", p,
                     lambda y: (self.R("f:new_debt", y, S), ctx.expect(y, L("new_debt"))))
            self.add(S, "m:cf_debt_principal", "Debt repaid", p,
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
            self.add(S, "m:cf_repurchases", "Share repurchases", p,
                     lambda y: (f"-{self.R('f:repurchases', y, S)}",
                                ctx.expect(y, L("repurchases", -1.0))))
            financing.append("m:cf_repurchases")
        self.add(S, "m:cff", "Financing cash flow", p,
                 lambda y: ("+".join(self.R(k, y, S) for k in financing) if financing else "0",
                            ctx.expect(y, L("cff"))))
        self.add(S, "m:cf_net_change", "Net change in cash", p,
                 lambda y: (f"{self.R('m:cfo', y, S)}+{self.R('m:cfi', y, S)}"
                            f"+{self.R('m:cff', y, S)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "cfo")
                                       + ctx.line(n, yy, "cfi") + ctx.line(n, yy, "cff"))))
        self.add(S, "m:cf_opening_cash", "Opening cash", p,
                 lambda y: (self._opening_cash(y),
                            ctx.expect(y, lambda n, yy: ctx.projected("cash")(n, self.prev(yy)))))
        self.add(S, "m:cf_closing_cash", "Closing cash", p,
                 lambda y: (f"{self.R('m:cf_opening_cash', y, S)}"
                            f"+{self.R('m:cf_net_change', y, S)}", ctx.expect(y, L("cash"))))
        self.add(S, "m:cf_fcf", "Free cash flow (CFO - capex)", p,
                 lambda y: (f"{self.R('m:cfo', y, S)}+{self.R('m:cf_capex', y, S)}",
                            ctx.expect(y, lambda n, yy: ctx.line(n, yy, "cfo")
                                       - ctx.line(n, yy, "capex"))))
        self.add(S, "chk:cash_tieout", "Cash tie-out: closing cash - balance sheet cash", p,
                 lambda y: (f"{self.R('m:cf_closing_cash', y, S)}-{self.R('m:cash', y, S)}",
                            ctx.expect(y, lambda n, yy: 0.0)), "check")

    def _opening_cash(self, y: int) -> str:
        """Opening cash = the prior year's balance-sheet cash. At FY+1, when the FY0
        reconciling row exists (#95), it is written as FY0 statement closing cash less
        restricted cash (the same amount), so the cash flow reads across FY0 -> FY+1."""
        prev, S = self.prev(y), CF  # noqa: N806
        closing, restricted = f"row:{S}:closing_cash", f"row:{S}:restricted_cash"
        if prev == self.origin and self.g.has(closing, prev) and self.g.has(restricted, prev):
            return f"{self.R(closing, prev, S)}-{self.R(restricted, prev, S)}"
        return self.R("m:cash", prev, S)

    # ---------------------------------------------------------------- F1-F5
    def plan_checks(self) -> None:
        ctx, F = self.ctx, FORECAST  # noqa: N806
        p = self.proj
        self.block("Model checks (Appendix B), recomputed independently of the lines above", p)
        self.add(F, "chk:F1_status", "F1 balance sheet balances (Balance Sheet check row)", p,
                 self._status("chk:F1"), "text", "exact")
        self.add(F, "chk:cash_tieout_status", "Cash tie-out (Cash Flow check row)", p,
                 self._status("chk:cash_tieout"), "text", "exact")

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
                              "repurchases)", p, f2, "check")
        self.add(F, "chk:F2_status", "F2 status", p, self._status("chk:F2"), "text", "exact")

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
                              "inventory - payables))", p, f3, "check")
        self.add(F, "chk:F3_status", "F3 status", p, self._status("chk:F3"), "text", "exact")
        pairs = [(k, o) for k, o, line in (("f:debt_principal", "f:debt_open", "debt"),
                                           ("f:ol_principal", "f:ol_open", "operating_lease"),
                                           ("f:fl_principal", "f:fl_open", "finance_lease"))
                 if ctx.has(line)]
        if pairs:
            self.add(F, "chk:F4", "F4 largest breach of 0 <= principal <= opening balance", p,
                     lambda y: ("MAX(0," + ",".join(
                         f"-{self.R(k, y, F)},{self.R(k, y, F)}-{self.R(o, y, F)}"
                         for k, o in pairs) + ")", ctx.expect(y, lambda n, yy: 0.0)), "check",
                     "exact")
            self.add(F, "chk:F4_status", "F4 status (exact)", p,
                     self._status("chk:F4", exact=True), "text", "exact")
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
                                  "haircut and floor", p, f5, "check")
            self.add(F, "chk:F5_status", "F5 status", p, self._status("chk:F5"), "text",
                     "exact")

    # ---------------------------------------------------------------- FY0 consistency
    def check_opening(self) -> list[str]:
        """The statement-sheet FY0 cells the projections start from must equal the Python
        model's opening position (forecast.opening_position)."""
        found = []
        want = dict(self.ctx.opening)
        want["revenue"] = self.ctx.last_revenue
        for line, value in want.items():
            key = OPENING_KEYS.get(line)
            if key is None or not self.g.has(key, self.origin):
                continue
            got = self.g.py(key, self.origin, ALL)
            if not isinstance(got, float) or abs(got - value) > 0.01:
                found.append(f"FY{self.origin} opening {line}: statement sheet {got!r} != "
                             f"model {value!r}")
        return found


def statement_keys(ctx: ModelContext, cols: Columns) -> set[str]:
    """The statement-sheet model keys the writer will project (no cells are written)."""
    wb = Workbook()
    sheets = {name: wb.create_sheet(name) for name in (FORECAST, IS, BS, CF)}
    writer = ModelWriter(Grid(cols, [], {}), ctx, sheets, None)
    writer.plan()
    return {line.key for line in writer.lines if line.sheet != FORECAST}


OPENING_KEYS = {"revenue": "m:revenue", "cash": "m:cash",
                "short_term_investments": "m:short_term_investments",
                "receivables": "m:receivables", "inventory": "m:inventory", "ppe": "m:ppe",
                "operating_rou": "m:operating_rou", "other_assets": "m:other_assets",
                "payables": "m:payables", "debt": "m:debt",
                "operating_lease": "m:operating_lease", "finance_lease": "m:finance_lease",
                "other_liabilities": "m:other_liabilities",
                "retained_earnings": "m:retained_earnings", "other_equity": "m:other_equity"}


def _col(n: int) -> str:
    return get_column_letter(n)


def _spec(ctx: ModelContext, name: str) -> ScenarioSpec:
    return next(s for s in ctx.specs if s.name == name)


def banner(ws: Worksheet, row: int, text: str, width: int) -> None:
    for c in range(1, width + 1):
        ws.cell(row=row, column=c).fill = BANNER_FILL
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = BOLD
