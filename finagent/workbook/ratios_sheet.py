"""The Ratios sheet: C.1 credit ratios and operating ratios as live formulas, historical and
projected (blueprint 8.9, 8.12, Appendix C.1; Phase 4d).

Each ratio is built from finagent.model.ratios: the same definition gives the formula (over the
statement cells) and the Python value (from the statement set and the projected lines), which is
the cell's expected value. A non-positive denominator shows "n.m." (C.1). A ratio whose inputs
have no value is a GAP cell (history) or an empty cell with the reason (projection: the line is
not modelled, e.g. current assets).
"""

from dataclasses import dataclass, field

from openpyxl.worksheet.worksheet import Worksheet

from finagent.model.ratio_inputs import (HIST_KEYS, OPTIONAL, equity_terms, historical_inputs,
                                         opening_values, period_days)
from finagent.model.ratios import NM_TEXT, RATIOS, RatioSpec, compute_all
from finagent.statements.build import StatementSet
from finagent.workbook.grid import ALL, Expected, Grid, MissingCell, section
from finagent.workbook.model import ModelContext

RATIOS_SHEET = "Ratios"
PROJ_KEYS = {
    "cash": ("m:cash", "cash"), "short_term_investments": ("m:short_term_investments",
                                                           "short_term_investments"),
    "receivables": ("m:receivables", "receivables"), "inventory": ("m:inventory", "inventory"),
    "payables": ("m:payables", "payables"),
    "operating_lease": ("m:operating_lease", "operating_lease"),
    "finance_lease": ("m:finance_lease", "finance_lease"),
    "total_liabilities": ("m:total_liabilities", None), "total_assets": ("m:total_assets", None),
    "ebit": ("m:ebit", "ebit"), "gross_interest": ("m:interest", "interest"),
    "cfo": ("m:cfo", "cfo"), "capex": ("f:capex", "capex"), "revenue": ("m:revenue", "revenue"),
    "cogs": ("m:cogs", "cogs"), "gross_profit": ("m:gross_profit", None),
    "net_income": ("m:net_income", "net_income"),
}
NOT_MODELLED = {"current_assets", "current_liabilities"}


@dataclass
class Period:
    """Ratio inputs of one column: key -> (A1 ref, value) per scenario."""

    refs: dict[str, str] = field(default_factory=dict)
    values: dict[str, dict[str, float]] = field(default_factory=dict)  # scenario -> key -> v
    absent: set[str] = field(default_factory=set)
    opening_refs: dict[str, str] = field(default_factory=dict)
    opening: dict[str, dict[str, float]] = field(default_factory=dict)
    days_ref: str | None = None
    days: float | None = None


class RatiosWriter:
    def __init__(self, grid: Grid, ws: Worksheet, s: StatementSet,
                 ctx: ModelContext | None) -> None:
        self.g, self.ws, self.s, self.ctx = grid, ws, s, ctx

    # ---------------------------------------------------------------- helpers rows
    def helpers(self, row: int) -> int:
        g, ws, s = self.g, self.ws, self.s
        section(ws, row, "Inputs to the ratios", g.cols.note)
        row += 1
        ws.cell(row=row, column=1, value="Period days (filing period; projected: days in year)")
        for y in g.cols.hist:
            days = period_days(s, y)
            if days is None:
                g.gap(ws, row, g.cols.col(y), f"FY{y}: period start is not known (BC1)")
            else:
                g.value(ws, row, g.cols.col(y), days, key="q:days", year=y, fmt="days",
                        note="end - start + 1 of the filing's fiscal period")
        if self.ctx is not None:
            for y in g.cols.proj:
                g.formula(ws, row, g.cols.col(y), g.absolute("d:days", None, RATIOS_SHEET),
                          {ALL: float(self.ctx.specs[0].days_in_year)}, key="q:days", year=y,
                          fmt="days")
        row += 1
        ws.cell(row=row, column=1, value="Funded debt (C.1)")
        for y in g.cols.hist:
            o = s.get(y, "funded_debt")
            if g.has("r:funded_debt", y) and o is not None and o.value is not None:
                g.formula(ws, row, g.cols.col(y), g.ref("r:funded_debt", y, RATIOS_SHEET),
                          {ALL: float(o.value)}, key="q:funded_debt", year=y)
            else:
                g.gap(ws, row, g.cols.col(y), f"FY{y}: funded debt has no accepted value")
        if self.ctx is not None:
            for y in [self.ctx.origin] + g.cols.proj:
                self._projected_funded_debt(row, y)
        row += 1
        ws.cell(row=row, column=1, value="Equity (I9 equity operands; projected: total equity)")
        for y in g.cols.hist:
            self._historical_equity(row, y)
        if self.ctx is not None:
            for y in g.cols.proj:
                g.formula(ws, row, g.cols.col(y), g.ref("m:total_equity", y, RATIOS_SHEET),
                          self.ctx.expect(y, lambda n, yy: self._line(n, yy, "retained_earnings")
                                          + self._line(n, yy, "other_equity")),
                          key="q:equity", year=y)
        return row + 2

    def _historical_equity(self, row: int, y: int) -> None:
        g, ws = self.g, self.ws
        terms = equity_terms(self.s, y)
        try:
            if not terms:
                raise MissingCell("I9 not evaluated")
            text = "".join(f"{'+' if t.startswith('-') else '-'}"
                           f"{g.ref('r:' + t[1:], y, RATIOS_SHEET)}" for t, _ in terms)
        except MissingCell as exc:
            g.gap(ws, row, g.cols.col(y), f"FY{y}: equity not available ({exc})")
            return
        value = -sum(float(v) for _, v in terms)
        g.formula(ws, row, g.cols.col(y), text.lstrip("+"), {ALL: value}, key="q:equity",
                  year=y)

    def _projected_funded_debt(self, row: int, y: int) -> None:
        g, ctx = self.g, self.ctx
        assert ctx is not None
        if not ctx.has("debt"):
            if y in g.cols.proj:
                g.empty(self.ws, row, g.cols.col(y), "no funded debt in the model")
            return
        if y == ctx.origin:
            return  # the history column already holds the reported funded debt
        include = self._fl_included()
        refs = [g.ref("m:debt", y, RATIOS_SHEET)] + (
            [g.ref("m:finance_lease", y, RATIOS_SHEET)] if include else [])
        g.formula(self.ws, row, g.cols.col(y), "+".join(refs),
                  ctx.expect(y, lambda n, yy: self._line(n, yy, "debt")
                             + (self._line(n, yy, "finance_lease") if include else 0.0)),
                  key="q:funded_debt", year=y)

    def _fl_included(self) -> bool:
        ctx = self.ctx
        assert ctx is not None
        debt = {o.line: o for o in ctx.base.opening}.get("debt")
        return debt is not None and debt.classification == "CALCULATION" \
            and ctx.has("finance_lease")

    def _line(self, n: str, y: int, line: str) -> float:
        ctx = self.ctx
        assert ctx is not None
        if y == ctx.origin:
            return ctx.opening[line]
        return ctx.line(n, y, line)

    # ---------------------------------------------------------------- periods
    def hist_period(self, y: int) -> Period:
        """The year's inputs (finagent.model.ratio_inputs), each referenced by its cell."""
        g, s = self.g, self.s
        p = Period()
        inputs = historical_inputs(s, y)
        values: dict[str, float] = {}
        for key, o in inputs.sources.items():
            if key in HIST_KEYS and g.has("r:" + o.metric, y):
                p.refs[key] = g.ref("r:" + o.metric, y, RATIOS_SHEET)
                values[key] = inputs.values[key]
        p.absent = set(inputs.absent)
        for key, q in (("funded_debt", "q:funded_debt"), ("equity", "q:equity")):
            if g.has(q, y):
                p.refs[key] = g.ref(q, y, RATIOS_SHEET)
                values[key] = self._q_value(q, y)
        prev = g.cols.previous(y)
        opening: dict[str, float] = {}
        if prev is not None:
            prior = self.hist_period_values(prev)
            for key in ("receivables", "inventory", "payables", "revenue"):
                if key in prior[0]:
                    p.opening_refs[key] = prior[1][key]
                    opening[key] = prior[0][key]
            if "revenue" in opening:
                p.refs["revenue_prior"] = p.opening_refs["revenue"]
                values["revenue_prior"] = opening["revenue"]
        if g.has("q:days", y):
            p.days_ref = g.ref("q:days", y, RATIOS_SHEET)
            p.days = period_days(s, y)
        p.values = {ALL: values}
        p.opening = {ALL: opening}
        return p

    def hist_period_values(self, y: int) -> tuple[dict[str, float], dict[str, str]]:
        values, refs = {}, {}
        for key, value in opening_values(self.s, y).items():
            metric = HIST_KEYS[key]
            if self.g.has("r:" + metric, y):
                values[key] = value
                refs[key] = self.g.ref("r:" + metric, y, RATIOS_SHEET)
        return values, refs

    def _q_value(self, q: str, y: int) -> float:
        if q == "q:funded_debt":
            o = self.s.get(y, "funded_debt")
            assert o is not None and o.value is not None
            return float(o.value)
        return -sum(float(v) for _, v in equity_terms(self.s, y))

    def proj_period(self, y: int) -> Period:
        g, ctx = self.g, self.ctx
        assert ctx is not None
        p = Period()
        prev = g.cols.previous(y)
        assert prev is not None
        for key, (cell, _) in PROJ_KEYS.items():
            if g.has(cell, y):
                p.refs[key] = g.ref(cell, y, RATIOS_SHEET)
            elif key in OPTIONAL:
                p.absent.add(key)
        if self._fl_included():
            p.absent.add("finance_lease")
            p.refs.pop("finance_lease", None)
        for key, q in (("funded_debt", "q:funded_debt"), ("equity", "q:equity")):
            if g.has(q, y):
                p.refs[key] = g.ref(q, y, RATIOS_SHEET)
        for key in ("receivables", "inventory", "payables"):
            if g.has(f"m:{key}", prev):
                p.opening_refs[key] = g.ref(f"m:{key}", prev, RATIOS_SHEET)
        p.refs["revenue_prior"] = g.ref("m:revenue", prev, RATIOS_SHEET)
        p.days_ref = g.ref("q:days", y, RATIOS_SHEET)
        p.days = float(ctx.specs[0].days_in_year)
        for n in ctx.verified:
            values: dict[str, float] = {}
            for key, (cell, line) in PROJ_KEYS.items():
                if key not in p.refs:
                    continue
                if line is not None:
                    values[key] = ctx.line(n, y, line)
            if "total_assets" in p.refs:
                values["total_assets"] = sum(ctx.line(n, y, l) for l in (
                    "cash", "short_term_investments", "receivables", "inventory", "ppe",
                    "operating_rou", "other_assets") if l in ctx.forecasts[n].years[0].lines)
                values["total_liabilities"] = sum(ctx.line(n, y, l) for l in (
                    "payables", "debt", "operating_lease", "finance_lease", "other_liabilities")
                    if l in ctx.forecasts[n].years[0].lines)
            if "gross_profit" in p.refs:
                values["gross_profit"] = ctx.line(n, y, "revenue") - ctx.line(n, y, "cogs")
            if "funded_debt" in p.refs:
                values["funded_debt"] = ctx.line(n, y, "debt") + (
                    ctx.line(n, y, "finance_lease") if self._fl_included() else 0.0)
            values["equity"] = ctx.line(n, y, "retained_earnings") + ctx.line(n, y,
                                                                               "other_equity")
            values["revenue_prior"] = self._line(n, prev, "revenue") if prev != ctx.origin \
                else ctx.last_revenue
            p.values[n] = values
            p.opening[n] = {k: self._line(n, prev, k) for k in p.opening_refs}
        return p

    # ---------------------------------------------------------------- ratio rows
    def write(self, row: int) -> int:
        g, ws = self.g, self.ws
        row = self.helpers(row)
        periods: dict[int, Period] = {y: self.hist_period(y) for y in g.cols.hist}
        if self.ctx is not None:
            periods.update({y: self.proj_period(y) for y in g.cols.proj})
        for group, title in (("credit", "Credit ratios (Appendix C.1)"),
                             ("operating", "Operating ratios")):
            section(ws, row, title, g.cols.note)
            row += 1
            for spec in (r for r in RATIOS if r.group == group):
                ws.cell(row=row, column=1, value=spec.label)
                for y, p in periods.items():
                    self._cell(spec, row, y, p)
                row += 1
            row += 1
        ws.cell(row=row, column=1, value=(
            "Ratios with a non-positive denominator show n.m. (C.1). DSO / DIO / DPO use average "
            "balances (opening + closing) / 2 and the period's days; the first historical year "
            "has no opening balance. The operating working-capital change (8.12) is closing - "
            "opening of receivables + inventory - payables (lines the filer does not report are "
            "left out). Projected current ratio, quick ratio and CFO / current "
            "liabilities are not modelled (the model has no current / non-current split)."))
        return row + 1

    def _cell(self, spec: RatioSpec, row: int, y: int, p: Period) -> None:
        g, ws = self.g, self.ws
        col = g.cols.col(y)
        projected = y in g.cols.proj
        uses = {k for _, k, _ in spec.numerator + spec.denominator}
        if projected and uses & NOT_MODELLED:
            g.empty(ws, row, col, "not modelled (no current / non-current split)")
            return
        results = {n: compute_all(v, p.absent, p.days, p.opening.get(n))
                   for n, v in p.values.items()}
        if not results:
            return
        first = next(iter(results.values()))[spec.ratio_id]
        if first.status in ("GAP", "NOT_APPLIED"):
            text = f"FY{y} {spec.ratio_id}: {first.status} ({first.detail})"
            (g.empty if projected or first.status == "NOT_APPLIED" else g.gap)(ws, row, col, text)
            return
        g.register(f"x:{spec.ratio_id}", y, RATIOS_SHEET, f"{g.cols.letter(y)}{row}")
        expected: Expected = {n: (r[spec.ratio_id].value if r[spec.ratio_id].status == "OK"
                                  else NM_TEXT) for n, r in results.items()}
        g.formula(ws, row, col, self._formula(spec, y, p), expected, fmt=spec.fmt,
                  tolerance="dollar" if spec.fmt == "dollars" else "ratio")

    def _expr(self, terms, p: Period) -> str:
        text = "".join(f"{'+' if sign > 0 else '-'}{p.refs[key]}" for sign, key, _ in terms
                       if key in p.refs)
        return text[1:] if text.startswith("+") else text

    def _formula(self, spec: RatioSpec, y: int, p: Period) -> str:
        nm = f'"{NM_TEXT}"'
        if spec.kind == "balance_change":  # (closing - opening) per reported line (#156)
            return "".join(f"{'+' if sign > 0 else '-'}({p.refs[key]}-{p.opening_refs[key]})"
                           for sign, key, _ in spec.numerator
                           if key in p.refs).lstrip("+")
        if spec.kind == "difference":
            parts = [(sign, self.g.ref(f"x:{key}", y, RATIOS_SHEET)) for sign, key, _ in
                     spec.numerator if self.g.has(f"x:{key}", y)]
            test = ",".join(f"ISNUMBER({ref})" for _, ref in parts)
            body = "".join(f"{'+' if s > 0 else '-'}{ref}" for s, ref in parts).lstrip("+")
            return f"IF(AND({test}),{body},{nm})"
        den = self._expr(spec.denominator, p)
        if spec.kind == "average_days":
            key = spec.numerator[0][1]
            return (f"IF({den}<=0,{nm},({p.opening_refs[key]}+{p.refs[key]})/2"
                    f"*{p.days_ref}/{den})")
        num = self._expr(spec.numerator, p)
        if spec.kind == "growth":
            return f"IF({den}<=0,{nm},{num}/{den}-1)"
        return f"IF(({den})<=0,{nm},({num})/({den}))"

