"""Cell registry, column map, styles and cell writers shared by every workbook sheet.

Every value or formula cell a sheet writes is registered under a key ("r:<metric>" for a reported
row, "m:<line>" for a model line, "f:<line>" for a Forecast schedule line, "d:<driver>" for an
effective driver) and a fiscal year (None for cells without a year). Formulas are built from the
registry, so a formula can only reference a cell that holds a value: a GAP cell is never
registered and so is never referenced (Excel would read a blank as 0; missing is never zero).

Each formula cell carries its expected value per scenario, computed in Python by the model (or by
finagent.model.ratios); the evaluator and the LibreOffice recalculation compare against it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

BLUE = Font(color="0000FF")
BLACK = Font(color="000000")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=13)
SECTION = Font(bold=True, color="FFFFFF")
SECTION_FILL = PatternFill("solid", fgColor="44546A")
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
GAP_FILL = PatternFill("solid", fgColor="FCE4D6")
NIL_FILL = PatternFill("solid", fgColor="EDEDED")  # #152: a component taken as nil
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
BANNER_FILL = PatternFill("solid", fgColor="F8CBAD")
OK_FILL = PatternFill("solid", fgColor="E2EFDA")
GREY = Font(color="808080", italic=True)
THIN_TOP = Border(top=Side(style="thin"))
WRAP = Alignment(wrap_text=True, vertical="top")

ALL = "*"  # expected value shared by every scenario
Expected = dict[str, float | str]  # scenario name (or ALL) -> value
Tolerance = str  # "dollar" ($0.01) | "ratio" (1e-9) | "exact"


class MissingCell(KeyError):
    """A formula needs a cell that holds no value (a GAP or a line the filer does not have)."""


@dataclass
class Columns:
    """Column map shared by the statement, Ratios and Forecast sheets: A label, B class,
    history (oldest first), then projections, then the note column."""

    hist: list[int]
    proj: list[int]
    first: int = 2

    @property
    def years(self) -> list[int]:
        return self.hist + self.proj

    def col(self, year: int) -> int:
        return self.first + self.years.index(year)

    def letter(self, year: int) -> str:
        return get_column_letter(self.col(year))

    @property
    def note(self) -> int:
        return self.first + len(self.years)

    def header(self, year: int) -> str:
        return f"FY{year}{'A' if year in self.hist else 'E'}"

    def previous(self, year: int) -> int | None:
        i = self.years.index(year)
        return self.years[i - 1] if i > 0 else None


def quote(sheet: str) -> str:
    return f"'{sheet}'" if " " in sheet else sheet


@dataclass
class Grid:
    cols: Columns
    scenarios: Sequence[str]
    formats: Mapping[str, str]
    refs: dict[tuple[str, int | None], tuple[str, str]] = field(default_factory=dict)
    signs: dict[tuple[str, int | None], int] = field(default_factory=dict)
    inputs: dict[tuple[str, str], float] = field(default_factory=dict)  # value cells
    expected: dict[tuple[str, str], Expected] = field(default_factory=dict)
    tolerance: dict[tuple[str, str], Tolerance] = field(default_factory=dict)
    gaps: list[tuple[str, str, str]] = field(default_factory=list)  # sheet, cell, reason
    nils: list[tuple[str, str, str]] = field(default_factory=list)  # sheet, cell, basis (#152)
    blanks: list[tuple[str, str, str]] = field(default_factory=list)  # not projected etc.
    problems: list[str] = field(default_factory=list)

    # ---- registry
    def register(self, key: str, year: int | None, sheet: str, coord: str,
                 sign: int = 1) -> None:
        """sign -1: the cell shows the key's value with the opposite sign (cash-flow outflows);
        references to the key then read -(cell)."""
        self.refs[(key, year)] = (sheet, coord)
        self.signs[(key, year)] = sign

    def alias(self, key: str, year: int | None, existing: str) -> None:
        self.refs[(key, year)] = self.refs[(existing, year)]
        self.signs[(key, year)] = self.signs.get((existing, year), 1)

    def py(self, key: str, year: int | None, scenario: str) -> float | str | None:
        """The Python value behind a registered key (sign applied), for one scenario."""
        cell = self.refs[(key, year)]
        sign = self.signs.get((key, year), 1)
        if cell in self.inputs:
            return sign * self.inputs[cell]
        expected = self.expected.get(cell, {})
        value = expected.get(scenario, expected.get(ALL))
        return sign * value if isinstance(value, float) else value

    def has(self, key: str, year: int | None = None) -> bool:
        return (key, year) in self.refs

    def ref(self, key: str, year: int | None, here: str) -> str:
        """A1 reference to the registered cell, sheet-qualified when on another sheet."""
        try:
            sheet, coord = self.refs[(key, year)]
        except KeyError as exc:
            raise MissingCell(f"{key} FY{year}") from exc
        text = coord if sheet == here else f"{quote(sheet)}!{coord}"
        return f"(-{text})" if self.signs.get((key, year), 1) < 0 else text

    def absolute(self, key: str, year: int | None, here: str) -> str:
        sheet, coord = self.refs[(key, year)]
        col = "".join(c for c in coord if c.isalpha())
        row = coord[len(col):]
        cell = f"${col}${row}"
        text = cell if sheet == here else f"{quote(sheet)}!{cell}"
        return f"(-{text})" if self.signs.get((key, year), 1) < 0 else text

    # ---- writers
    def value(self, ws: Worksheet, row: int, col: int, value: float | str | None,
              key: str | None = None, year: int | None = None, fmt: str = "dollars",
              font: Font = BLUE, note: str | None = None, sign: int = 1) -> str:
        """An input cell. No comment: notes go on GAP cells only (status and sources are on
        the Verification and Sources sheets)."""
        del note
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = font
        if fmt in self.formats and isinstance(value, (int, float)):
            cell.number_format = self.formats[fmt]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.inputs[(ws.title, cell.coordinate)] = float(value)
        if key is not None:
            self.register(key, year, ws.title, cell.coordinate, sign)
        return cell.coordinate

    def formula(self, ws: Worksheet, row: int, col: int, formula: str, expected: Expected,
                key: str | None = None, year: int | None = None, fmt: str = "dollars",
                tolerance: Tolerance = "dollar", note: str | None = None,
                font: Font = BLACK) -> str:
        del note
        cell = ws.cell(row=row, column=col, value="=" + formula.lstrip("="))
        cell.font = font
        cell.number_format = self.formats.get(fmt, "General")
        self.expected[(ws.title, cell.coordinate)] = dict(expected)
        self.tolerance[(ws.title, cell.coordinate)] = tolerance
        if key is not None:
            self.register(key, year, ws.title, cell.coordinate)
        return cell.coordinate

    def gap(self, ws: Worksheet, row: int, col: int, reason: str) -> None:
        """A visibly empty cell with a note: never 0, never registered."""
        cell = ws.cell(row=row, column=col)
        cell.value = None
        cell.fill = GAP_FILL
        short = reason if len(reason) <= 140 else reason[:137] + "..."
        cell.comment = Comment(f"GAP: {short}", "FinAgent", width=300, height=90)
        self.gaps.append((ws.title, cell.coordinate, reason))

    def nil(self, ws: Worksheet, row: int, col: int, basis: str) -> str:
        """#152: a component no channel read this year but read in other years, shown as an
        input of 0 with a grey fill and a NIL note (never a reported value)."""
        coordinate = self.value(ws, row, col, 0.0)
        cell = ws.cell(row=row, column=col)
        cell.fill = NIL_FILL
        short = basis if len(basis) <= 140 else basis[:137] + "..."
        cell.comment = Comment(f"NIL (#152): {short}", "FinAgent", width=300, height=90)
        self.nils.append((ws.title, coordinate, basis))
        return coordinate

    def empty(self, ws: Worksheet, row: int, col: int, reason: str) -> None:
        """An empty cell for a line that does not exist there (not projected, not modelled);
        no note (the reason is kept for the manifest only)."""
        cell = ws.cell(row=row, column=col)
        cell.value = None
        self.blanks.append((ws.title, cell.coordinate, reason))


# ---------------------------------------------------------------- sheet helpers

def title(ws: Worksheet, text: str, subtitle: str = "") -> None:
    ws.cell(row=1, column=1, value=text).font = TITLE
    if subtitle:
        ws.cell(row=2, column=1, value=subtitle).font = GREY


SUBTOTAL = Font(bold=True)
PCT = Font(italic=True, color="595959")
DIVIDER = Side(style="medium", color="000000")


def year_header(ws: Worksheet, row: int, cols: Columns, years: Sequence[int] | None = None,
                label: str = "$ millions") -> None:
    """Header band: label, then FY....A / FY....E; a vertical line between actuals and
    estimates."""
    cell = ws.cell(row=row, column=1, value=label)
    cell.font = BOLD
    cell.fill = HEADER_FILL
    for year in years if years is not None else cols.years:
        cell = ws.cell(row=row, column=cols.col(year), value=cols.header(year))
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="right")


def divider(ws: Worksheet, cols: Columns, first_row: int, last_row: int) -> None:
    """The vertical line before the first projected column."""
    if not cols.proj:
        return
    col = cols.col(cols.proj[0])
    for r in range(first_row, last_row + 1):
        cell = ws.cell(row=r, column=col)
        cell.border = Border(left=DIVIDER, top=cell.border.top, bottom=cell.border.bottom)


def section(ws: Worksheet, row: int, text: str, width: int) -> None:
    for c in range(1, width + 1):
        ws.cell(row=row, column=c).fill = SECTION_FILL
    ws.cell(row=row, column=1, value=text).font = SECTION


def widths(ws: Worksheet, cols: Columns | None, label: int = 46, note: bool = False) -> None:
    ws.column_dimensions["A"].width = label
    if cols is not None:
        for year in cols.years:
            ws.column_dimensions[cols.letter(year)].width = 12
        if note:
            ws.column_dimensions[get_column_letter(cols.note)].width = 60
