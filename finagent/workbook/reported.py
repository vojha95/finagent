"""Block A of the statement sheets: the reported detail, history columns only (8.9, BC21).

REPORTED_FACT values with an accepted value are blue inputs. CALCULATION values are live
formulas: a formula derivation from its own signed terms (the observation's detail lists the
metrics used), a face-block sum (e.g. total liabilities with no total line) from its component
rows, shown beneath it. A value without an accepted value (GAP, DISAGREE, CONFLICT, INVALID,
IDENTITY_FAILED, ...) is a visibly empty cell with a note, never 0. Rows the filer never reports
are left out. The balance check (I9) and the cash tie-out (I10) are formulas over the identity's
own operands.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl.worksheet.worksheet import Worksheet

from finagent.config import WorkbookLayout, WorkbookRow
from finagent.records import IdentityResult, Observation
from finagent.statements.build import StatementSet
from finagent.statements.derive import usable
from finagent.statements.verify_set import NOT_REPORTED
from finagent.workbook.grid import ALL, BLACK, BOLD, GREY, Grid, MissingCell, section

_DETAIL = re.compile(r"^[+-][a-z0-9_]+( [+-][a-z0-9_]+)*$")
FIRST_ROW = 5  # rows 1-3 title, 4 year header


@dataclass
class PlannedRow:
    sheet: str
    row: int
    metric: str
    label: str
    link_to: str | None = None  # the sheet of the metric's first appearance
    components: list[str] = field(default_factory=list)  # component labels (face-block sums)


def shown(s: StatementSet, metric: str) -> bool:
    """A row is shown when the filer has an observation of it other than NOT_REPORTED."""
    return any(o is not None and not (o.value is None and o.reason == NOT_REPORTED)
               for o in (s.get(y, metric) for y in s.years()))


def _label(metric: str, row: WorkbookRow, labels: dict[str, str]) -> str:
    return row.label or labels.get(metric) or metric.replace("_", " ").capitalize()


def _component_labels(s: StatementSet, metric: str) -> list[str]:
    found: list[str] = []
    for y in s.years():
        o = s.get(y, metric)
        for _, label, _ in (o.components if o is not None else ()):
            if label.strip().lower() not in [f.strip().lower() for f in found]:
                found.append(label)
    return found


def plan(s: StatementSet, layout: WorkbookLayout, labels: dict[str, str],
         start: int = FIRST_ROW + 1) -> tuple[dict[str, list[PlannedRow]], dict[str, int]]:
    """Rows of block A per sheet, and the next free row on each sheet."""
    planned: dict[str, list[PlannedRow]] = {}
    first_sheet: dict[str, str] = {}
    next_row: dict[str, int] = {}
    for sheet, rows in layout.reported_rows.items():
        r = start
        planned[sheet] = []
        for entry in rows:
            if not shown(s, entry.metric):
                continue
            link = first_sheet.get(entry.metric) if entry.link else None
            p = PlannedRow(sheet, r, entry.metric, _label(entry.metric, entry, labels), link)
            if link is None:
                first_sheet.setdefault(entry.metric, sheet)
                p.components = _component_labels(s, entry.metric)
            planned[sheet].append(p)
            r += 1 + len(p.components)
        next_row[sheet] = r
    return planned, next_row


def register(grid: Grid, s: StatementSet, planned: dict[str, list[PlannedRow]]) -> None:
    """Register every block-A cell that will hold a value, before any formula is written."""
    for rows in planned.values():
        for p in rows:
            key = f"l:{p.metric}" if p.link_to else f"r:{p.metric}"
            for y in grid.cols.hist:
                o = s.get(y, p.metric)
                if o is not None and usable(o, provisional=False) and o.value is not None:
                    grid.register(key, y, p.sheet, f"{grid.cols.letter(y)}{p.row}")
                    for n, (_, label, value) in enumerate(o.components):
                        index = _index(p.components, label)
                        if value is not None:
                            grid.register(f"c:{p.metric}:{index}", y, p.sheet,
                                          f"{grid.cols.letter(y)}{p.row + 1 + index}")


def _index(labels: Sequence[str], label: str) -> int:
    lowered = [x.strip().lower() for x in labels]
    return lowered.index(label.strip().lower())


def gap_reason(o: Observation | None, year: int) -> str:
    if o is None:
        return f"FY{year}: no observation"
    text = f"FY{year}: {o.status} ({o.reason})"
    if o.status in ("VERIFIED", "SINGLE_CHANNEL") and not o.accepted:
        text += " - value not accepted"
    if o.document_value is not None or o.tag_value is not None:
        text += f"; document {o.document_value}, tag {o.tag_value}"
    if o.candidates:
        text += f"; candidates {list(o.candidates)}"
    if o.detail:
        text += f"; {o.detail[:200]}"
    return text


def _note(o: Observation) -> str | None:
    bits = []
    if o.status != "VERIFIED":
        bits.append(f"{o.status} ({o.reason})")
    if o.flags:
        bits.append("flags: " + ", ".join(o.flags))
    if o.basis:
        bits.append(f"basis: {o.basis}")
    return "; ".join(bits) or None


def derivation_formula(grid: Grid, o: Observation, here: str) -> str:
    """The derivation as a formula over its own terms (raises MissingCell)."""
    if not _DETAIL.match(o.detail or ""):
        raise MissingCell(f"{o.metric}: no term list in detail {o.detail!r}")
    parts = []
    for token in o.detail.split():
        sign, metric = token[0], token[1:]
        parts.append(f"{sign}{grid.ref('r:' + metric, o.fiscal_year, here)}")
    text = "".join(parts)
    return text[1:] if text.startswith("+") else text


def write(grid: Grid, ws: Worksheet, s: StatementSet, rows: Sequence[PlannedRow]) -> None:
    width = grid.cols.note
    section(ws, FIRST_ROW, "Reported (as filed): blue = REPORTED_FACT input, black = "
                           "CALCULATION formula, shaded = GAP", width)
    for p in rows:
        ws.cell(row=p.row, column=1, value=p.label)
        ws.cell(row=p.row, column=2, value="LINK" if p.link_to else "")
        kinds = set()
        for y in grid.cols.hist:
            col = grid.cols.col(y)
            o = s.get(y, p.metric)
            if p.link_to:
                try:
                    source = grid.ref(f"r:{p.metric}", y, ws.title)
                except MissingCell:
                    grid.gap(ws, p.row, col, f"see {p.link_to}: " + gap_reason(o, y))
                    continue
                assert o is not None and o.value is not None
                grid.formula(ws, p.row, col, source, {ALL: float(o.value)})
                continue
            if o is None or not usable(o, provisional=False) or o.value is None:
                grid.gap(ws, p.row, col, gap_reason(o, y))
                continue
            if o.classification != "CALCULATION":
                kinds.add("RF")
                grid.value(ws, p.row, col, float(o.value), note=_note(o))
                continue
            kinds.add("CALC")
            _write_calculation(grid, ws, p, o, col)
        ws.cell(row=p.row, column=2).value = "LINK" if p.link_to else "/".join(sorted(kinds))
        for index, label in enumerate(p.components):
            r = p.row + 1 + index
            ws.cell(row=r, column=1, value=f"    of which: {label}").font = GREY
            ws.cell(row=r, column=2, value="RF")
            for y in grid.cols.hist:
                o = s.get(y, p.metric)
                match = [v for _, lab, v in (o.components if o else ())
                         if lab.strip().lower() == label.strip().lower()]
                if match and match[0] is not None:
                    grid.value(ws, r, grid.cols.col(y), float(match[0]),
                               note="face-block component (document row; tag-checked)")


def _write_calculation(grid: Grid, ws: Worksheet, p: PlannedRow, o: Observation,
                       col: int) -> None:
    assert o.value is not None
    expected = {ALL: float(o.value)}
    try:
        if o.components:
            refs = [grid.ref(f"c:{p.metric}:{_index(p.components, label)}", o.fiscal_year,
                             ws.title) for _, label, value in o.components]
            formula = "+".join(refs)
        else:
            formula = derivation_formula(grid, o, ws.title)
    except MissingCell as exc:
        grid.problems.append(f"{ws.title} {p.metric} FY{o.fiscal_year}: CALCULATION written as "
                             f"a value, an input is not on the sheets ({exc})")
        grid.value(ws, p.row, col, float(o.value), font=BLACK,
                   note=f"CALCULATION ({o.detail[:120]}); an input is not shown as a cell")
        return
    grid.formula(ws, p.row, col, formula, expected, note=_note(o))


# ---------------------------------------------------------------- identity checks (history)

def identity(s: StatementSet, year: int, identity_id: str) -> IdentityResult | None:
    found = [r for r in s.identities if r.fiscal_year == year and r.identity_id == identity_id
             and r.subject is None]
    return found[0] if found else None


def identity_formula(grid: Grid, r: IdentityResult, here: str) -> str:
    """Signed sum of the identity's operand cells; each cell must hold the operand's value."""
    text = "".join(f"{term[0]}{grid.ref('r:' + term[1:], r.fiscal_year, here)}"
                   for term, _ in r.operands)
    return text[1:] if text.startswith("+") else text


def write_identity_rows(grid: Grid, ws: Worksheet, s: StatementSet, row: int,
                        identity_id: str, label: str) -> int:
    """Residual row and status row for I9 / I10 in every history column; returns next row."""
    ws.cell(row=row, column=1, value=label).font = BOLD
    ws.cell(row=row, column=2, value="CHECK")
    ws.cell(row=row + 1, column=1, value=f"{identity_id} status")
    for y in grid.cols.hist:
        col = grid.cols.col(y)
        r = identity(s, y, identity_id)
        if r is None or r.residual is None or not r.operands:
            reason = f"{identity_id} FY{y}: {r.status if r else 'not evaluated'}"
            grid.empty(ws, row, col, reason + (f" ({r.detail[:160]})" if r and r.detail else ""))
            continue
        try:
            if not all(_cell_matches(grid, s, y, term) for term, _ in r.operands):
                raise MissingCell("operand not shown as an accepted value")
            formula = identity_formula(grid, r, ws.title)
        except MissingCell as exc:
            grid.empty(ws, row, col, f"{identity_id} FY{y}: {r.status}, residual {r.residual}; "
                                     f"not computable from the cells shown ({exc})")
            continue
        coord = grid.formula(ws, row, col, formula, {ALL: float(r.residual)},
                             key=f"chk:{identity_id}", year=y)
        tol = grid.absolute("d:tolerance", None, ws.title)
        grid.formula(ws, row + 1, col, f'IF(ABS({coord})<={tol},"OK","FAIL")',
                     {ALL: "OK" if abs(float(r.residual)) <= 0.01 else "FAIL"},
                     fmt="text", tolerance="exact")
    return row + 2


def _cell_matches(grid: Grid, s: StatementSet, year: int, term: str) -> bool:
    o = s.get(year, term[1:])
    return grid.has("r:" + term[1:], year) and o is not None and o.value is not None


def equity_terms(s: StatementSet, year: int) -> list[tuple[str, Decimal]]:
    """The equity operands of I9 (every operand except total assets and total liabilities)."""
    r = identity(s, year, "I9")
    if r is None:
        return []
    return [(t, v) for t, v in r.operands if t not in ("+total_assets", "-total_liabilities")]
