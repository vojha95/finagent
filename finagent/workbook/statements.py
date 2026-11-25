"""The statement sheets: one continuous table per statement (blueprint 8.9; Phase 4d; BC21).

Each line item is one row across the historical (A) and projected (E) columns, in the order of
configs/workbook.yaml. History:
- a reported line: the accepted value in blue (a CALCULATION metric as its derivation formula,
  a face-block total as the sum of its component rows); no accepted value -> a GAP cell (empty,
  shaded, one short note), never 0;
- a subtotal with components: a formula over the component rows when they sum exactly to the
  reported value, otherwise the reported value in blue;
- a model line with no single reported row (residual): a formula over the reported rows;
- a link row: a link to the row on another sheet.
Projection: the integrated-model line of the row (finagent.workbook.model), always a formula;
rows the model does not project (indented detail, other non-operating, FX) stay blank.
Percent rows and the bottom check rows are formulas in every column.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.worksheet.worksheet import Worksheet

from finagent.records import Observation
from finagent.statements.build import StatementSet
from finagent.statements.derive import is_nil, usable
from finagent.statements.verify_set import NOT_REPORTED
from finagent.workbook.grid import (ALL, BOLD, PCT, SUBTOTAL, Grid, MissingCell, divider,
                                    year_header)
from finagent.workbook.layout import TemplateRow, WorkbookLayout
from finagent.workbook.reported import (DETAIL_PATTERN, derivation_terms, equity_terms,
                                        gap_reason)

FIRST_ROW = 6  # rows 1-3 title, 5 year header
TOP = Side(style="thin")


def copy(font: Font, **changes: object) -> Font:
    """A copy of a font with some attributes changed."""
    attrs = {k: getattr(font, k) for k in ("name", "size", "bold", "italic", "underline",
                                           "strike", "color", "vertAlign")}
    attrs.update(changes)
    return Font(**attrs)  # type: ignore[arg-type]
VALUED = ("value", "formula", "nil")  # nil: #152, an input of 0 with a note


@dataclass
class HistCell:
    """One historical cell before it is written."""

    kind: str  # value | formula | nil | gap | notrep | none
    value: float | None = None  # as displayed (sign applied)
    exact: Decimal | None = None
    build: Callable[[], str] | None = None
    reason: str = ""

    @property
    def valued(self) -> bool:
        return self.kind in VALUED


@dataclass
class Placed:
    sheet: str
    row: int
    spec: TemplateRow | None  # None for a detail metric row
    label: str
    kind: str
    metric: str | None = None  # detail metric
    component: tuple[str, int] | None = None  # (parent metric, index) for a face-block row


@dataclass
class StatementSheets:
    grid: Grid
    s: StatementSet
    layout: WorkbookLayout
    labels: Mapping[str, str]
    no_cogs: bool
    model_keys: set[str]
    origin: int | None = None  # FY0: the only column of an `at: origin` row
    cells: dict[tuple[str, str, int], HistCell] = field(default_factory=dict)
    placed: dict[str, list[Placed]] = field(default_factory=dict)
    bound: set[tuple[str, int]] = field(default_factory=set)  # (metric, year) on a main row
    problems: list[str] = field(default_factory=list)
    _sheet_now: str = ""

    # ---------------------------------------------------------------- resolution
    @property
    def years(self) -> list[int]:
        return self.grid.cols.hist

    def rows(self, sheet: str) -> dict[str, TemplateRow]:
        return {r.row_id: r for r in self.layout.statements[sheet]}

    def active(self, spec: TemplateRow) -> bool:
        return spec.when is None or (spec.when == "no_cogs") == self.no_cogs

    def cell(self, sheet: str, row_id: str, year: int) -> HistCell:
        key = (sheet, row_id, year)
        if key not in self.cells:
            self.cells[key] = HistCell("none")  # guards cycles
            spec = self.rows(sheet)[row_id]
            self.cells[key] = self._resolve(sheet, spec, year) if self.active(spec) \
                else HistCell("none")
        return self.cells[key]

    def _resolve(self, sheet: str, spec: TemplateRow, year: int) -> HistCell:
        if spec.kind in ("header", "detail", "pct"):
            return HistCell("none")
        if spec.at == "origin" and year != self.origin:
            return HistCell("none")
        if spec.link:
            target = self.cell(spec.link[0], spec.link[1], year)
            if not target.valued:
                return HistCell("gap", reason=f"see {spec.link[0]}: {target.reason}")
            ref = lambda: self.grid.ref(f"row:{spec.link[0]}:{spec.link[1]}", year,  # noqa
                                        sheet)
            return HistCell("formula", target.value, target.exact, ref)
        if spec.special == "debt":
            return self._debt(year)
        if spec.special == "equity":
            return self._equity(year)
        if spec.residual:
            return self._sum(sheet, spec.residual, year)
        if spec.metric is None:
            return HistCell("none")
        metric = self.metric(spec.metric, year)
        if metric is None:
            return HistCell("gap", reason=f"FY{year}: no {spec.metric[6:]} chosen")
        o = self.s.get(year, metric)
        if spec.components:
            summed = self._sum(sheet, spec.components, year)
            absent = o is None or (o.value is None and o.reason == NOT_REPORTED)
            if summed.valued and (absent or (usable(o, False) and o.value == summed.exact)):
                return summed
        return self.metric_cell(o, year, metric, spec.sign)

    def metric(self, name: str, year: int) -> str | None:
        if name.startswith("basis:"):
            return self.s.bases.get(year, {}).get(name[6:])
        return name

    def metric_cell(self, o: Observation | None, year: int, metric: str,
                    sign: int = 1) -> HistCell:
        if o is None:
            return HistCell("gap", reason=f"FY{year}: no observation of {metric}")
        if not (usable(o, False) and o.value is not None):
            kind = "notrep" if o.value is None and o.reason == NOT_REPORTED else "gap"
            return HistCell(kind, reason=gap_reason(o, year))
        if is_nil(o):
            return HistCell("nil", 0.0, Decimal(0), reason=o.basis or "nil (#152)")
        value = float(o.value) * sign
        exact = o.value * sign
        if o.classification != "CALCULATION":
            return HistCell("value", value, exact)
        return HistCell("formula", value, exact, lambda: self._calculation(o, year, sign))

    def _calculation(self, o: Observation, year: int, sign: int) -> str:
        """A CALCULATION metric as a formula over its own inputs (raises MissingCell)."""
        if o.components:
            text = "+".join(self.grid.ref(f"c:{o.metric}:{i}", year, self._sheet_now)
                            for i in range(len(o.components)))
        else:
            if not DETAIL_PATTERN.match(o.detail or ""):
                raise MissingCell(f"{o.metric}: no term list")
            text = "".join(f"{sign_}{self.grid.ref('r:' + m, year, self._sheet_now)}"
                           for sign_, m in derivation_terms(o.detail)).lstrip("+")
        return f"-({text})" if sign < 0 else text

    def _sum(self, sheet: str, terms, year: int) -> HistCell:
        used = []
        for t in terms:
            c = self.cell(t.sheet or sheet, t.row, year)
            if c.valued:
                used.append((t, c))
            elif t.optional and c.kind in ("notrep", "none"):
                continue
            else:
                return HistCell("gap", reason=f"FY{year}: {t.row} has no value")
        if not used:
            return HistCell("none")
        exact = sum((Decimal(t.sign) * c.exact for t, c in used), Decimal(0))
        value = sum(t.sign * c.value for t, c in used)

        def build() -> str:
            refs = (self.grid.ref(f"row:{t.sheet or sheet}:{t.row}", year, self._sheet_now)
                    for t, _ in used)
            text = "".join(f"{'+' if t.sign > 0 else '-'}{ref}"
                           for (t, _), ref in zip(used, refs))
            return text.lstrip("+")
        return HistCell("formula", value, exact, build)

    def _debt(self, year: int) -> HistCell:
        """Debt as the model opens it: funded debt, net of finance leases when the debt concept
        includes them (forecast.opening_position)."""
        funded = self.s.get(year, "funded_debt")
        lease = self.s.get(year, "finance_lease_liability")
        cell = self.metric_cell(funded, year, "funded_debt")
        included = funded is not None and "FINANCE_LEASES_INCLUDED" in funded.flags
        if cell.valued and included and lease is not None and usable(lease, False) \
                and lease.value is not None:
            here = lambda: self._sheet_now  # noqa: E731
            return HistCell("formula", cell.value - float(lease.value),
                            cell.exact - lease.value,
                            lambda: f"{self.grid.ref('r:funded_debt', year, here())}"
                                    f"-{self.grid.ref('r:finance_lease_liability', year, here())}")
        if cell.valued:
            cell.reason = "binds:funded_debt"
        return cell

    def _equity(self, year: int) -> HistCell:
        terms = equity_terms(self.s, year)
        if not terms:
            return HistCell("gap", reason=f"FY{year}: I9 not evaluated (equity unknown)")
        cells = [(t, self.metric_cell(self.s.get(year, t[1:]), year, t[1:])) for t, _ in terms]
        if not all(c.valued for _, c in cells):
            return HistCell("gap", reason=f"FY{year}: an equity operand has no value")
        if len(cells) == 1:
            c = cells[0][1]
            c.reason = f"binds:{terms[0][0][1:]}"
            return c
        exact = sum((-Decimal(1 if t.startswith("+") else -1) * c.exact for t, c in cells),
                    Decimal(0))

        def build() -> str:
            return "".join(f"{'+' if t.startswith('-') else '-'}"
                           f"{self.grid.ref('r:' + t[1:], year, self._sheet_now)}"
                           for t, _ in cells).lstrip("+")
        return HistCell("formula", float(exact), exact, build)

    # ---------------------------------------------------------------- planning
    def shown(self, sheet: str, spec: TemplateRow) -> bool:
        if not self.active(spec):
            return False
        if spec.kind == "header":
            return True
        if spec.kind == "pct":
            return all(self.shown(sheet, self.rows(sheet)[r]) for r in spec.pct.values())
        if spec.kind == "check":
            return True
        if spec.model and spec.model in self.model_keys:
            return True
        return any(self.cell(sheet, spec.row_id, y).kind in ("value", "formula", "gap")
                   for y in self.years)

    def bound_metric(self, spec: TemplateRow, year: int) -> str | None:
        c = self.cells.get((self._sheet_now, spec.row_id, year))
        if c is None or not c.valued:
            return None
        if c.reason.startswith("binds:"):
            return c.reason[6:]
        if spec.metric and not spec.residual and not spec.link and not spec.special:
            return self.metric(spec.metric, year)
        return None

    def plan(self) -> None:
        """Resolve every row, decide what is shown, and give each row its sheet row."""
        for sheet in self.layout.statements:
            for spec in self.layout.statements[sheet]:
                for y in self.years:
                    self.cell(sheet, spec.row_id, y)
        for sheet, specs in self.layout.statements.items():
            self._sheet_now = sheet
            for spec in specs:
                for y in self.years:
                    m = self.bound_metric(spec, y)
                    if m:
                        self.bound.add((m, y))
        for sheet, specs in self.layout.statements.items():
            self._sheet_now = sheet
            r = FIRST_ROW
            placed: list[Placed] = []
            for spec in specs:
                if spec.kind != "detail" and self.shown(sheet, spec):
                    placed.append(Placed(sheet, r, spec, spec.label, spec.kind))
                    r += 1
                    r = self._components(sheet, spec, placed, r)
                for metric in spec.details:
                    if self._detail_shown(metric):
                        placed.append(Placed(sheet, r, None, self._label(metric), "detail",
                                             metric))
                        r += 1
            self.placed[sheet] = placed

    def _components(self, sheet: str, spec: TemplateRow, placed: list[Placed], r: int) -> int:
        """Face-block component rows under a total derived from them."""
        if not spec.metric or spec.metric.startswith("basis:"):
            return r
        width = max((len(o.components) for o in (self.s.get(y, spec.metric) for y in self.years)
                     if o is not None), default=0)
        for i in range(width):
            labels = {o.components[i][1] for o in (self.s.get(y, spec.metric) for y in self.years)
                      if o is not None and len(o.components) > i}
            placed.append(Placed(sheet, r, None, "of which: " + " / ".join(sorted(labels)),
                                 "detail", component=(spec.metric, i)))
            r += 1
        return r

    def _detail_shown(self, metric: str) -> bool:
        for y in self.years:
            o = self.s.get(y, metric)
            if o is None or (metric, y) in self.bound:
                continue
            if usable(o, False) and o.value is not None:
                return True
        return False

    def _label(self, metric: str) -> str:
        return self.labels.get(metric) or metric.replace("_", " ").capitalize()

    # ---------------------------------------------------------------- registration
    def register(self, origin: int | None,
                 model_rows: dict[tuple[str, str], tuple[int, str]]) -> None:
        """Register every valued historical cell; model_rows gets (sheet, model key) ->
        (sheet row, row id) for the projected lines. The model keys are registered at the
        origin year: FY0 of the projection is the historical cell."""
        g = self.grid
        for sheet, placed in self.placed.items():
            self._sheet_now = sheet
            for p in placed:
                for y in self.years:
                    coord = f"{g.cols.letter(y)}{p.row}"
                    if p.spec is not None:
                        c = self.cells[(sheet, p.spec.row_id, y)]
                        if not c.valued:
                            continue
                        g.register(f"row:{sheet}:{p.spec.row_id}", y, sheet, coord)
                        m = self.bound_metric(p.spec, y)
                        if m:
                            g.register(f"r:{m}", y, sheet, coord, p.spec.sign)
                        if p.spec.model and y == origin:
                            g.register(p.spec.model, y, sheet, coord)
                    elif p.component is not None:
                        metric, i = p.component
                        o = self.s.get(y, metric)
                        if o is not None and len(o.components) > i \
                                and o.components[i][2] is not None:
                            g.register(f"c:{metric}:{i}", y, sheet, coord)
                    elif p.metric and (p.metric, y) not in self.bound:
                        o = self.s.get(y, p.metric)
                        if o is not None and usable(o, False) and o.value is not None:
                            g.register(f"r:{p.metric}", y, sheet, coord)
                if p.spec is not None and p.spec.model:
                    model_rows[(sheet, p.spec.model)] = (p.row, p.spec.row_id)

    # ---------------------------------------------------------------- writing
    def write(self, ws: Worksheet) -> None:
        g, sheet = self.grid, ws.title
        self._sheet_now = sheet
        year_header(ws, FIRST_ROW - 1, g.cols)
        last = FIRST_ROW
        for p in self.placed[sheet]:
            last = p.row
            label = ws.cell(row=p.row, column=1, value=p.label)
            if p.kind == "header":
                label.font = BOLD
                continue
            if p.kind == "detail":
                label.alignment = Alignment(indent=2)
                self._write_detail(ws, p)
                continue
            if p.kind in ("subtotal", "total"):
                label.font = SUBTOTAL
            if p.kind in ("pct", "check"):
                label.font = PCT
            if p.kind == "pct":
                continue  # written after the model lines (they need projected values)
            assert p.spec is not None
            for y in self.years:
                self._write_hist(ws, p, y)
        divider(ws, g.cols, FIRST_ROW - 1, last)

    def _write_hist(self, ws: Worksheet, p: Placed, y: int) -> None:
        g = self.grid
        assert p.spec is not None
        c = self.cells[(ws.title, p.spec.row_id, y)]
        col = g.cols.col(y)
        fmt = "check" if p.kind == "check" else "dollars"
        if c.kind == "value":
            g.value(ws, p.row, col, c.value, fmt=fmt)
        elif c.kind == "nil":
            g.nil(ws, p.row, col, c.reason)
        elif c.kind == "formula":
            assert c.build is not None and c.value is not None
            try:
                formula = c.build()
            except MissingCell as exc:
                self.problems.append(f"{ws.title} {p.spec.row_id} FY{y}: CALCULATION has an "
                                     f"input not on the sheets ({exc})")
                g.value(ws, p.row, col, c.value, fmt=fmt)
            else:
                g.formula(ws, p.row, col, formula, {ALL: c.value}, fmt=fmt)
        elif c.kind in ("gap", "notrep") and p.kind != "check":
            g.gap(ws, p.row, col, c.reason or f"FY{y}: no accepted value")
        self._style(ws, p, col)

    def _write_detail(self, ws: Worksheet, p: Placed) -> None:
        g = self.grid
        for y in self.years:
            col = g.cols.col(y)
            if p.component is not None:
                metric, i = p.component
                o = self.s.get(y, metric)
                if o is not None and len(o.components) > i and o.components[i][2] is not None:
                    g.value(ws, p.row, col, float(o.components[i][2]))
                continue
            assert p.metric is not None
            if (p.metric, y) in self.bound:
                continue
            o = self.s.get(y, p.metric)
            if o is None or (o.value is None and o.reason == NOT_REPORTED):
                continue
            c = self.metric_cell(o, y, p.metric)
            if c.kind == "value":
                g.value(ws, p.row, col, c.value)
            elif c.kind == "nil":
                g.nil(ws, p.row, col, c.reason)
            elif c.kind == "formula":
                try:
                    g.formula(ws, p.row, col, c.build(), {ALL: c.value})  # type: ignore[misc]
                except MissingCell as exc:
                    self.problems.append(f"{ws.title} {p.metric} FY{y}: {exc}")
                    g.value(ws, p.row, col, c.value)
            else:
                g.gap(ws, p.row, col, c.reason)

    def _style(self, ws: Worksheet, p: Placed, col: int) -> None:
        cell = ws.cell(row=p.row, column=col)
        if p.kind in ("subtotal", "total"):
            cell.font = copy(cell.font, bold=True)
            cell.border = Border(top=TOP, left=cell.border.left)
        if p.kind in ("pct", "check"):
            cell.font = copy(cell.font, italic=True)

    def style_projection(self, ws: Worksheet) -> None:
        """Bold / top border / italic on the projected cells written by the model."""
        g = self.grid
        for p in self.placed[ws.title]:
            if p.kind in ("subtotal", "total", "pct", "check"):
                for y in g.cols.proj:
                    self._style(ws, p, g.cols.col(y))
        divider(ws, g.cols, FIRST_ROW - 1, max((p.row for p in self.placed[ws.title]),
                                               default=FIRST_ROW))

    def write_pct(self, ws: Worksheet, scenarios: Sequence[str]) -> None:
        """Percent rows in every column, after the model lines exist."""
        g, sheet = self.grid, ws.title
        for p in self.placed[sheet]:
            if p.kind != "pct":
                continue
            assert p.spec is not None
            pct = p.spec.pct
            for y in g.cols.years:
                if "growth" in pct:
                    prev = g.cols.previous(y)
                    if prev is None:
                        continue
                    num, den = (f"row:{sheet}:{pct['growth']}", y), \
                        (f"row:{sheet}:{pct['growth']}", prev)
                else:
                    num, den = (f"row:{sheet}:{pct['num']}", y), (f"row:{sheet}:{pct['den']}", y)
                if not (g.has(*num) and g.has(*den)):
                    continue
                names = [ALL] if y in g.cols.hist else list(scenarios)
                expected: dict[str, float | str] = {}
                for n in names:  # a scenario without a projection has no expected value
                    a, b = g.py(*num, n), g.py(*den, n)
                    if isinstance(a, float) and isinstance(b, float):
                        expected[n] = "n.m." if b <= 0 else (a / b - 1 if "growth" in pct
                                                             else a / b)
                if not expected:
                    continue
                n_ref, d_ref = g.ref(*num, sheet), g.ref(*den, sheet)
                body = f"{n_ref}/{d_ref}-1" if "growth" in pct else f"{n_ref}/{d_ref}"
                g.formula(ws, p.row, g.cols.col(y), f'IF({d_ref}<=0,"n.m.",{body})',
                          expected, fmt="percent", tolerance="ratio")
                self._style(ws, p, g.cols.col(y))
