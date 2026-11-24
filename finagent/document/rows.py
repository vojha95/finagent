"""Row builder: face-statement tables in the clean document -> RowInstance records (blueprint 8.3).

Steps 2 and 3 of 8.3: label = first non-numeric cell; figures kept as raw text, grouped under
the period column whose header spans them; style flags from inline CSS; rows without figures
become parent headers; column headers -> period end dates; the unit line -> unit_text (from a
header cell, the title block, the text before the table, or the filing-wide sentence, BC12).
A row labelled with a period caption ("Fiscal Year Ended January 31,") belongs to the column
header and is never a row; a row with neither a label nor a figure is skipped.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from lxml import etree

from finagent.document.locate import (
    StatementTables,
    document_tables,
    item_8_table_start,
    local_name,
    locate_statements,
)
from finagent.ixbrl.tagstore import parse_document, table_grid
from finagent.records import ColumnKind, RowInstance, Statement

_MONTHS = (
    "january february march april may june july august september october november december"
).split()
# A month name, full or abbreviated ("Jan", "Sept."); _month_number reads either.
_MONTH = rf"(?:{'|'.join(_MONTHS)}|(?:{'|'.join(m[:3] for m in _MONTHS)}|sept)\b\.?)"
# Day and year may be split by a comma with no space ("September 27,2025": a line break in
# the cell) or by spaces alone ("Dec 27 2025").
_DAY_YEAR = r"(\d{1,2})(?:,\s*|\s+)((?:19|20)\d{2})"
_MONTH_DAY = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}})\b", re.IGNORECASE)
_FULL_DATE = re.compile(rf"\b({_MONTH})\s+{_DAY_YEAR}\b", re.IGNORECASE)
# A caption cell that ends in its column's date: "Years ended December 31, 2025".
_ENDS_WITH_DATE = re.compile(rf"\b({_MONTH})\s+{_DAY_YEAR}$", re.IGNORECASE)
# After a month and day, or after a full date: a year, or a list of further years.
_YEAR_AFTER = re.compile(r",?\s*((?:19|20)\d{2})\b")
_YEAR_LIST_TAIL = re.compile(r"(?:,?\s*(?:and\s+)?(?:19|20)\d{2}\b)+", re.IGNORECASE)
_YEAR_CELL = re.compile(r"(?:fy\s*)?((?:19|20)\d{2})\*?", re.IGNORECASE)
# A figure cell: digits, or a dash standing for nil, with currency and number punctuation.
# A space may separate the currency sign from an opening bracket ("$ (0.03)").
_FIGURE = re.compile(r"(?:[$€£(]\s*)*(?:\d[\d,.]*|[—–-])\s*\)?\s*%?")
_NUMERIC_ONLY = re.compile(r"[\d$€£(),.%\s—–\-]*")
_CLOSER = re.compile(r"\)\s*%?|%")
_UNIT_WORD = r"(?:millions|thousands|billions)\b(?!\s+of\s+shares)"
_UNIT_PREFIX = r"(?:(?:in|amounts in|dollars in|\$ in)\s+)?"
# The unit line in brackets, anywhere: "(in millions)", "(MILLIONS, EXCEPT PER SHARE DATA)",
# "(Millions of dollars)". "(Shares in thousands)" is not a unit line (no prefix before it).
_UNIT_LINE = re.compile(rf"\(\s*{_UNIT_PREFIX}{_UNIT_WORD}[^)]*\)", re.IGNORECASE)
# Without brackets, only as the start of a short header or title-block cell: "Millions of
# Dollars", "in millions, except per share data", "Dollars in millions except per share amounts"
# (issue #42). Never searched in running text.
_UNIT_CELL = re.compile(rf"(?:dollars\s+)?{_UNIT_PREFIX}{_UNIT_WORD}", re.IGNORECASE)
_UNIT_CELL_CHARS = 160
# A title block: short text blocks and up to this many short tables with no figures just
# before a statement table ("AT&T Inc. | Dollars in millions except per share amounts").
_TITLE_BLOCK_TABLES = 3
_TITLE_BLOCK_CHARS = 400
# A unit sentence for the whole filing (BC12): "All amounts are presented in millions of U.S.
# dollars, unless otherwise specified."
_FILING_UNIT = re.compile(
    rf"\ball\s+(?:dollar\s+)?amounts\s+(?:are\s+)?(?:presented|stated|expressed|reported|shown)"
    rf"\s+in\s+{_UNIT_WORD}(?:[^.]|(?<=\b[A-Z])\.)*",  # "U.S." does not end the sentence
    re.IGNORECASE,
)
# A column caption naming the period, not a line item: "Fiscal Year Ended January 31,",
# "Years ended December 31, 2025", "As of June 30,", "December 31,".
_PERIOD_CAPTION = re.compile(
    rf"(?:(?:for\s+the\s+)?(?:fiscal\s+)?(?:years?|(?:three|six|nine|twelve)\s+months"
    rf"|\w+\s+weeks|periods?)\s+end(?:ed|ing)\s+|as\s+of\s+|at\s+)?"
    rf"{_MONTH}\s+\d{{1,2}},?(?:\s*(?:19|20)\d{{2}})?",
    re.IGNORECASE,
)
_LENGTH = re.compile(r"(-?\d*\.?\d+)\s*(pt|px|in|em|rem|%|cm|mm)?", re.IGNORECASE)
# Points per unit. Percentages are relative to the label cell; ~4pt per percent is a fixed
# scale that keeps indents comparable within one filing, which is all ranking needs.
_POINTS = {"pt": 1.0, "px": 0.75, "in": 72.0, "em": 10.0, "rem": 10.0, "%": 4.0, "cm": 28.35,
           "mm": 2.835, None: 0.75}
_PRECEDING_TEXT_CHARS = 600
# Zero-width characters some filers put in otherwise empty cells; they are not visible text.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
# A percentage sub-column: its header names a percentage, or every figure in it is one (BC11).
_PERCENT_HEADER = re.compile(r"%|\bpercent", re.IGNORECASE)
_PERCENT_FIGURE = re.compile(r"\(?-?\d[\d,.]*\)?\s*%")


@dataclass(frozen=True)
class _Cell:
    element: etree._Element
    row: int
    column: int
    colspan: int
    text: str


@dataclass(frozen=True)
class _Period:
    start: int
    end: int
    period_end: date | None
    kind: ColumnKind = "amount"


def _normalise(text: str) -> str:
    return " ".join(_ZERO_WIDTH.sub("", text).replace(" ", " ").split())


def parse_style(style: str | None) -> dict[str, str]:
    """Inline CSS -> {property: value}, lower-cased property names."""
    declarations: dict[str, str] = {}
    for part in (style or "").split(";"):
        name, _, value = part.partition(":")
        if value:
            declarations[name.strip().lower()] = value.strip()
    return declarations


def to_points(value: str) -> float:
    match = _LENGTH.match(value.strip())
    if not match:
        return 0.0
    number, unit = match.groups()
    return float(number) * _POINTS[unit.lower() if unit else None]


def _left_padding(style: dict[str, str]) -> float:
    if "padding-left" in style:
        return to_points(style["padding-left"])
    parts = style.get("padding", "").split()
    if len(parts) == 4:
        return to_points(parts[3])
    if len(parts) in (2, 3):
        return to_points(parts[1])
    if len(parts) == 1:
        return to_points(parts[0])
    return 0.0


def _text_chain(cell: etree._Element) -> list[etree._Element]:
    """The cell and its descendants down to the first element carrying visible text."""
    for element in cell.iter():
        if element.text and element.text.strip():
            chain = [element, *element.iterancestors()]
            return chain[: chain.index(cell) + 1] if cell in chain else [cell]
    return [cell]


def indent_points(cell: etree._Element) -> float:
    """Left offset of the cell's text: padding-left, margin-left and text-indent, summed."""
    total = 0.0
    for element in _text_chain(cell):
        style = parse_style(element.get("style"))
        total += _left_padding(style)
        total += to_points(style.get("margin-left", "0"))
        total += to_points(style.get("text-indent", "0"))
    return total


def _is_bold_style(style: dict[str, str]) -> bool:
    weight = style.get("font-weight", "").lower()
    return weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)


def is_bold(cell: etree._Element, row: etree._Element) -> bool:
    chain = _text_chain(cell)
    if any(local_name(e) in ("b", "strong") for e in chain):
        return True
    return any(_is_bold_style(parse_style(e.get("style"))) for e in [*chain, row])


def _has_border(element: etree._Element, side: str) -> bool:
    style = parse_style(element.get("style"))
    value = style.get(f"border-{side}", "") or style.get("border", "")
    return bool(value) and "none" not in value.lower() and not value.startswith("0")


def _span(cell: etree._Element, name: str) -> int:
    try:
        return max(1, int(cell.get(name, "1")))
    except ValueError:
        return 1


def _rows(table: etree._Element) -> list[etree._Element]:
    return [r for r in table.iter() if local_name(r) == "tr"]


def _row_cells(row: etree._Element, row_index: int, grid: dict) -> list[_Cell]:
    cells = []
    for element in row:
        if local_name(element) not in ("td", "th"):
            continue
        _, column = grid[element]
        cells.append(
            _Cell(element, row_index, column, _span(element, "colspan"),
                  _normalise("".join(element.itertext())))
        )
    return cells


def _is_figure(text: str) -> bool:
    return bool(text) and bool(_FIGURE.fullmatch(text)) and bool(re.search(r"[\d—–-]", text))


def is_period_caption(label: str) -> bool:
    """True for a label that names a column's period ("Fiscal Year Ended January 31,")."""
    return bool(_PERIOD_CAPTION.fullmatch(label.strip().rstrip(":").strip()))


def _label_cell(cells: list[_Cell]) -> _Cell | None:
    """First-cell rule: the first cell whose text is not purely numeric."""
    for cell in cells:
        if cell.text and not _NUMERIC_ONLY.fullmatch(cell.text):
            return cell
    return None


def _month_number(name: str) -> int:
    """1-12 for a full or abbreviated month name ("September", "Sep", "Sept.")."""
    return [m[:3] for m in _MONTHS].index(name.lower()[:3]) + 1


def _period_end(year: int, month_day: tuple[str, str] | None) -> date | None:
    if month_day is None:
        return None
    month = _month_number(month_day[0])
    try:
        return date(year, month, int(month_day[1]))
    except ValueError:
        return None


def _fits_label(end: date, label: int) -> bool:
    """A fiscal year labelled Y ends in Y, or early in Y+1 (a January or February year end)."""
    return end.year == label or (end.year == label + 1 and end.month <= 2)


def stated_dates(text: str) -> list[date]:
    """Every full date written in the text, in order; a year list after a date adds one date
    per year ("December 31, 2025 and 2024" -> 2025-12-31, 2024-12-31)."""
    dates: list[date] = []
    for match in _FULL_DATE.finditer(text):
        month_day = (match.group(1), match.group(2))
        years = [match.group(3)]
        tail = _YEAR_LIST_TAIL.match(text, match.end())
        if tail:
            years += re.findall(r"(?:19|20)\d{2}", tail.group(0))
        dates += [d for y in years if (d := _period_end(int(y), month_day)) is not None]
    return dates


def title_dates(preceding_text: str, labels: list[int]) -> list[date] | None:
    """One period end per bare-year column, from full dates listed just above the table.

    "For the Years Ended November 2, 2025, October 27, 2024, and October 29, 2023" gives three
    dates for columns 2025, 2024, 2023. The last len(labels) stated dates are used, only when
    they run in the same order as the labels and each fits its label; otherwise None. A date
    stated for one year is never carried to another.
    """
    if not labels:
        return None
    dates = stated_dates(preceding_text)[-len(labels):]
    if len(dates) != len(labels) or not all(_fits_label(d, y) for d, y in zip(dates, labels)):
        return None
    ordered = all((a < b) == (x < y) for (a, x), (b, y) in zip(zip(dates, labels),
                                                               zip(dates[1:], labels[1:])))
    return dates if ordered and len(set(dates)) == len(dates) else None


def title_month_day(preceding_text: str) -> tuple[str, str] | None:
    """A month and day written without a year just above the table ("December 31,"), or None.

    Like a "December 31," cell over the year columns, it names every column's period. A date
    with a year (a period end of one year, an auditor's report date) never does: it is used
    only through title_dates.
    """
    accepted = None
    for match in _MONTH_DAY.finditer(preceding_text):
        if not _YEAR_AFTER.match(preceding_text, match.end()):
            accepted = (match.group(1), match.group(2))
    return accepted


def parse_periods(
    header_cells: list[_Cell],
    preceding_text: str,
    fiscal_periods: Mapping[int, date] | None = None,
) -> list[_Period]:
    """Period columns from header rows: year (or full-date) cells and their column spans.

    A bare year takes its month and day from a phrase such as "December 31" in the header.
    Failing that, full dates listed just above the table give each bare year its own end
    (title_dates; 52/53-week years differ), or a month and day written there without a year
    applies to every column (title_month_day). Dates the document states always win; one
    stated for one year is never carried to another. A bare year still undated takes
    the filing's fiscal period with that label (fiscal_periods, {fiscal year: period end},
    from the evidence store; 8.5). Otherwise the period end is unknown (None), never a guess.
    A caption cell ending in a full date ("Years ended December 31, 2025") is a period column
    too, unless its span overlaps another period column.
    """
    header_text = " ".join(c.text for c in header_cells)
    phrases = _MONTH_DAY.findall(header_text)
    month_day = phrases[-1] if phrases else None
    labels = [int(y.group(1)) for c in header_cells
              if not _FULL_DATE.fullmatch(c.text.rstrip(",")) and (y := _YEAR_CELL.fullmatch(c.text))]
    listed = None if month_day else title_dates(preceding_text, labels)
    if month_day is None and listed is None:
        month_day = title_month_day(preceding_text)
    periods = []
    bare = 0
    for cell in header_cells:
        full = _FULL_DATE.fullmatch(cell.text.rstrip(","))
        year = _YEAR_CELL.fullmatch(cell.text)
        if full:
            end = _period_end(int(full.group(3)), (full.group(1), full.group(2)))
        elif year:
            label = int(year.group(1))
            end = listed[bare] if listed else _period_end(label, month_day)
            if end is None and fiscal_periods:
                end = fiscal_periods.get(label)
            bare += 1
        else:
            continue
        periods.append(_Period(cell.column, cell.column + cell.colspan - 1, end))
    for cell in header_cells:
        caption = _ENDS_WITH_DATE.search(cell.text.rstrip(","))
        if caption is None or _FULL_DATE.fullmatch(cell.text.rstrip(",")):
            continue
        start, end_column = cell.column, cell.column + cell.colspan - 1
        if any(p.start <= end_column and start <= p.end for p in periods):
            continue
        end = _period_end(int(caption.group(3)), (caption.group(1), caption.group(2)))
        periods.append(_Period(start, end_column, end))
    return sorted(periods, key=lambda p: p.start)


def _is_percent_column(header: str, body: list[str]) -> bool:
    figures = [t for t in body if t]
    if _PERCENT_HEADER.search(header):
        return True
    return bool(figures) and all(_PERCENT_FIGURE.fullmatch(t) for t in figures)


def split_percent_columns(
    periods: list[_Period], sub_header: list[_Cell], body_rows: list[list[_Cell]]
) -> list[_Period]:
    """Split each period column at its sub-column headings ("Amount" | "% Sales"; BC11).

    A period whose span holds two or more sub-headings is divided at them. A sub-column whose
    heading names a percentage, or whose every figure is a percentage, becomes a percent column
    (period None, never an amount); the others keep the period. Periods without a percentage
    sub-column are returned unchanged.
    """
    result: list[_Period] = []
    for period in periods:
        subs = sorted(
            (c for c in sub_header if c.text and period.start <= c.column <= period.end),
            key=lambda c: c.column,
        )
        if len(subs) < 2:
            result.append(period)
            continue
        bounds = [
            (sub.column, subs[i + 1].column - 1 if i + 1 < len(subs) else period.end)
            for i, sub in enumerate(subs)
        ]
        bounds[0] = (period.start, bounds[0][1])
        parts = []
        for sub, (start, end) in zip(subs, bounds):
            body = [
                " ".join(c.text for c in cells if start <= c.column <= end and c.text)
                for cells in body_rows
            ]
            percent = _is_percent_column(sub.text, body)
            parts.append(
                _Period(start, end, None, "percent") if percent
                else _Period(start, end, period.period_end)
            )
        if all(p.kind == "amount" for p in parts):
            result.append(period)
        else:
            result.extend(parts)
    return result


def _fallback_periods(value_cells: Iterable[_Cell]) -> list[_Period]:
    """Without a parsable header, each column holding a figure is its own period (end unknown)."""
    columns = sorted({c.column for c in value_cells if _is_figure(c.text)})
    return [_Period(c, c, None) for c in columns]


def _period_of(column: int, periods: list[_Period]) -> int | None:
    for index, period in enumerate(periods):
        if period.start <= column <= period.end:
            return index
    return None


def _period_before(column: int, periods: list[_Period]) -> int | None:
    """The period column ending nearest before this grid column."""
    before = [i for i, p in enumerate(periods) if p.end < column]
    return max(before, key=lambda i: periods[i].end) if before else None


def preceding_texts(root: etree._Element) -> dict[int, str]:
    """For each table index, the last _PRECEDING_TEXT_CHARS of visible text before it."""
    texts: dict[int, str] = {}
    buffer = ""
    table_index = 0
    inside: list[etree._Element] = []
    for event, element in etree.iterwalk(root, events=("start", "end")):
        name = local_name(element)
        if event == "start":
            if name == "table":
                if not inside:
                    texts[table_index] = buffer[-_PRECEDING_TEXT_CHARS:]
                table_index += 1
                inside.append(element)
            elif not inside and element.text:
                buffer = (buffer + " " + element.text)[-_PRECEDING_TEXT_CHARS * 2:]
        else:
            if name == "table" and inside and inside[-1] is element:
                inside.pop()
            if not inside and element.tail:
                buffer = (buffer + " " + element.tail)[-_PRECEDING_TEXT_CHARS * 2:]
    return {i: _normalise(t) for i, t in texts.items()}


def unit_in_cell(text: str) -> str | None:
    """The unit line a header or title-block cell states, or None.

    A bracketed unit line may sit anywhere in the cell; an unbracketed one must open a short
    cell ("Millions of Dollars"), so a label such as "Shares in millions" is never one.
    """
    text = _normalise(text)
    bracketed = _UNIT_LINE.search(text)
    if bracketed:
        return bracketed.group(0)
    balanced = text.count(")") <= text.count("(")  # not the tail of a split "(in | millions)"
    if balanced and len(text) <= _UNIT_CELL_CHARS and _UNIT_CELL.match(text):
        return text
    return None


_BLOCKS = ("p", "div")


def _is_leaf_block(element: etree._Element) -> bool:
    return local_name(element) in _BLOCKS and not any(
        local_name(d) in (*_BLOCKS, "table") for d in element.iterdescendants()
    )


def _cell_texts(table: etree._Element) -> list[str]:
    texts = (_normalise(" ".join(c.itertext())) for c in table.iter()
             if local_name(c) in ("td", "th"))
    return [t for t in texts if t]


def title_block_cells(root: etree._Element, table_indices: Iterable[int]) -> dict[int, list[str]]:
    """{table index: texts of its title block, nearest first} (8.3; issue #42).

    The title block is what sits directly before a statement table: short text blocks outside
    tables ("Consolidated Balance Sheet", "Millions of dollars, except per-share amounts") and
    short tables with no figures ("AT&T Inc. | Dollars in millions except per share amounts").
    Walking back from the table, tables enclosing it are skipped; the walk stops at a table
    with a figure, at a text block or table longer than a title, or after
    _TITLE_BLOCK_TABLES tables.
    """
    wanted = set(table_indices)
    segments: list[tuple[str, object]] = []  # ("table", (index, element)) or ("text", str)
    positions: dict[int, int] = {}
    table_index = 0
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if local_name(element) == "table":
            if table_index in wanted:
                positions[table_index] = len(segments)
            segments.append(("table", (table_index, element)))
            table_index += 1
        elif _is_leaf_block(element) and not any(
            local_name(a) == "table" for a in element.iterancestors()
        ):
            text = _normalise(" ".join(element.itertext()))
            if text:
                segments.append(("text", text))
    blocks: dict[int, list[str]] = {}
    for index, position in positions.items():
        target = segments[position][1][1]
        ancestors = set(target.iterancestors())
        texts: list[str] = []
        tables_taken = 0
        for kind, value in reversed(segments[:position]):
            if kind == "text":
                if len(value) > _TITLE_BLOCK_CHARS:
                    break
                texts.append(value)
                continue
            _, table = value
            if table in ancestors:
                continue
            cells = _cell_texts(table)
            if any(_is_figure(t) and re.search(r"\d", t) for t in cells):
                break
            if sum(len(t) for t in cells) > _TITLE_BLOCK_CHARS:
                break
            texts.extend(cells)
            tables_taken += 1
            if tables_taken == _TITLE_BLOCK_TABLES:
                break
        blocks[index] = texts
    return blocks


def find_unit_line(
    header_cells: Iterable[str], title_cells: Iterable[str], preceding: str
) -> str | None:
    """The table's unit line (8.3 step 3; issue #42), or None when the table states none.

    Searched in order: the column-header cells, the title-block cells, then the bracketed unit
    line nearest the table in the text before it. Never a default scale.
    """
    header = [h for h in header_cells if h]
    for text in [*header, *title_cells]:
        unit = unit_in_cell(text)
        if unit:
            return unit
    joined = _UNIT_LINE.search(" ".join(header))  # a unit line split across header cells
    if joined:
        return joined.group(0)
    nearest = list(_UNIT_LINE.finditer(preceding))
    return nearest[-1].group(0) if nearest else None


def filing_unit_sentence(root: etree._Element, from_table: int) -> str | None:
    """The filing-wide unit sentence at or after table position from_table (BC12), or None.

    Only text outside tables is read. When sentences at or after that point disagree on the
    scale, there is no filing-wide unit (None).
    """
    found: list[str] = []
    table_count = 0
    inside = 0
    buffer: list[str] = []

    def flush() -> None:
        if table_count >= from_table and buffer:
            found.extend(m.group(0) for m in _FILING_UNIT.finditer(_normalise(" ".join(buffer))))
        buffer.clear()

    for event, element in etree.iterwalk(root, events=("start", "end")):
        if not isinstance(element.tag, str):
            continue
        is_table = local_name(element) == "table"
        if event == "start":
            if is_table:
                if not inside:
                    flush()
                table_count += 1
                inside += 1
            elif not inside and element.text:
                buffer.append(element.text)
        else:
            if is_table:
                inside -= 1
            if not inside and element.tail:
                buffer.append(element.tail)
    flush()
    scales = {re.search(r"millions|thousands|billions", s, re.IGNORECASE).group(0).lower()
              for s in found}
    return found[0] if len(scales) == 1 else None


@dataclass
class _RawRow:
    label: str
    indent: float
    bold: bool
    total: bool
    texts: tuple[str, ...]
    periods: tuple[date | None, ...]
    spans: tuple[tuple[int, int], ...]
    kinds: tuple[ColumnKind, ...]
    table_index: int
    source_row: int
    unit_text: str | None
    is_header: bool


def _table_rows(
    table: etree._Element,
    table_index: int,
    preceding: str,
    inherited: list[_Period] | None,
    fiscal_periods: Mapping[int, date] | None = None,
    title_cells: Iterable[str] = (),
    fallback_unit: str | None = None,
) -> tuple[list[_RawRow], list[_Period]]:
    grid = table_grid(table)
    rows = _rows(table)
    all_cells = [_row_cells(row, i, grid) for i, row in enumerate(rows)]

    first_value_row = next(
        (
            i
            for i, cells in enumerate(all_cells)
            if (label := _label_cell(cells)) is not None
            and not is_period_caption(label.text)  # a caption row is part of the header
            and any(_is_figure(c.text) and not _YEAR_CELL.fullmatch(c.text) for c in cells)
        ),
        len(rows),
    )
    header_cells = [c for cells in all_cells[:first_value_row] for c in cells if c.text]
    periods = parse_periods(header_cells, preceding, fiscal_periods)
    from_header = bool(periods)
    if not periods:
        periods = inherited or _fallback_periods(
            c for cells in all_cells[first_value_row:] for c in cells
        )
    # Content starts after the last header row that names a period, so section headers such
    # as "Current assets:" between the year row and the first figure row are kept as rows.
    content_start = next(
        (
            i + 1
            for i in range(first_value_row - 1, -1, -1)
            if any(_YEAR_CELL.fullmatch(c.text) or _FULL_DATE.fullmatch(c.text.rstrip(","))
                   for c in all_cells[i])
        ),
        first_value_row,
    )
    # A sub-heading row between the period row and the first figure row ("Amount" | "% Sales")
    # may split each period into an amount and a percent column (BC11).
    if from_header:
        for sub_row in range(content_start, first_value_row):
            split = split_percent_columns(
                periods, all_cells[sub_row], all_cells[first_value_row:]
            )
            if split != periods:
                periods, content_start = split, sub_row + 1
                break
    unit_text = find_unit_line(
        (c.text for c in header_cells), title_cells, preceding
    ) or fallback_unit

    raw: list[_RawRow] = []
    border_above = False
    for row_index in range(content_start, len(rows)):
        row, cells = rows[row_index], all_cells[row_index]
        if "collapse" in parse_style(row.get("style")).get("visibility", ""):
            continue
        label_cell = _label_cell(cells)
        if label_cell is not None and is_period_caption(label_cell.text):
            continue  # a period caption is never a row (8.3)
        texts: list[list[str]] = [[] for _ in periods]
        borders = False
        for cell in cells:
            if cell is label_cell:
                continue
            index = _period_of(cell.column, periods)
            if index is None and _CLOSER.fullmatch(cell.text):
                # A closing bracket or % set in its own cell just after a period column.
                index = _period_before(cell.column, periods)
            if index is None:
                continue
            borders = borders or _has_border(cell.element, "top")
            if cell.text:
                texts[index].append(cell.text)
        for index in range(1, len(texts)):
            # A closer opening a period column belongs to the previous figure if that one
            # left a bracket open ("(5" | ") 7" -> "(5 )" | "7").
            previous = " ".join(texts[index - 1])
            if texts[index] and _CLOSER.fullmatch(texts[index][0]) and previous.count("(") > previous.count(")"):
                texts[index - 1].append(texts[index].pop(0))
        joined = tuple(" ".join(parts) for parts in texts)
        has_figures = any(_is_figure(t) for t in joined)
        if label_cell is None and not has_figures:
            # A spacer row: remember a rule drawn under it, which marks the next row a total.
            border_above = any(_has_border(c.element, "bottom") for c in cells) or border_above
            continue
        label = label_cell.text if label_cell is not None else ""
        bold = is_bold(label_cell.element, row) if label_cell is not None else False
        raw.append(
            _RawRow(
                label=label,
                indent=indent_points(label_cell.element) if label_cell is not None else 0.0,
                bold=bold,
                total=has_figures
                and (borders or border_above or label.lower().startswith("total")),
                texts=joined,
                periods=tuple(p.period_end for p in periods),
                spans=tuple((p.start, p.end) for p in periods),
                kinds=tuple(p.kind for p in periods),
                table_index=table_index,
                source_row=row_index,
                unit_text=unit_text,
                is_header=not has_figures,
            )
        )
        border_above = False
    return raw, periods


def _comparable(label: str) -> str:
    return label.replace("’", "'").rstrip(":").strip().lower()


def _names(total_label: str, header_label: str) -> bool:
    """True when a total row's label names a section header (case and apostrophes ignored)."""
    header = _comparable(header_label)
    return bool(header) and header in _comparable(total_label)


def _indent_levels(raw: list[_RawRow]) -> dict[float, int]:
    distinct = sorted({round(r.indent) for r in raw})
    return {value: level for level, value in enumerate(distinct)}


def header_chain(headers: list[tuple[float, str]], index: int) -> tuple[str, ...]:
    """Labels of the open header at `index` and of each enclosing one, nearest first.

    An enclosing header is the nearest earlier open header with a strictly smaller indent than
    the last one taken, so a sibling section header at the same indent is never an ancestor.
    """
    if index < 0 or index >= len(headers):
        return ()
    chain = [headers[index][1]]
    indent = headers[index][0]
    for earlier_indent, label in reversed(headers[:index]):
        if earlier_indent < indent:
            chain.append(label)
            indent = earlier_indent
    return tuple(chain)


def statement_rows(
    root: etree._Element,
    located: StatementTables,
    accession: str,
    tables: list[etree._Element] | None = None,
    preceding: dict[int, str] | None = None,
    fiscal_periods: Mapping[int, date] | None = None,
    filing_unit: str | None = None,
) -> list[RowInstance]:
    """RowInstances for one located statement (continuation tables merged when headers match).

    fiscal_periods ({fiscal year: period end}) dates bare-year columns the document leaves
    undated (parse_periods). filing_unit is the filing-wide unit sentence (BC12): used only for
    a statement whose first table states no unit of its own. A continuation table without a
    unit takes the statement's.
    """
    tables = tables if tables is not None else document_tables(root)
    preceding = preceding if preceding is not None else preceding_texts(root)
    raw: list[_RawRow] = []
    periods: list[_Period] | None = None
    statement_unit = filing_unit
    title_blocks = title_block_cells(root, located.table_indices)
    for table_index in located.table_indices:
        rows, table_periods = _table_rows(
            tables[table_index], table_index, preceding.get(table_index, ""), periods,
            fiscal_periods, title_blocks.get(table_index, []), statement_unit,
        )
        if periods is None and rows:
            statement_unit = rows[0].unit_text
        if periods is not None and [(p.period_end, p.kind) for p in table_periods] != [
            (p.period_end, p.kind) for p in periods
        ]:
            break  # 8.3 step 3: only merge continuation tables with identical headers
        raw.extend(rows)
        periods = periods or table_periods

    levels = _indent_levels(raw)
    # Open section headers. A total row closes the open header it names ("Total current
    # assets" closes "Current assets:") and takes it as parent; a subtotal naming something
    # else closes nothing. Rows after a closed section fall back to the enclosing one.
    headers: list[tuple[float, str]] = []
    instances: list[RowInstance] = []
    for row in raw:
        if row.is_header:
            headers.append((row.indent, row.label))
            continue
        closed = next(
            (i for i in range(len(headers) - 1, -1, -1) if _names(row.label, headers[i][1])),
            None,
        ) if row.total else None
        if closed is not None:
            chain = header_chain(headers, closed)
            del headers[closed:]
        else:
            index = next(
                (i for i in range(len(headers) - 1, -1, -1) if headers[i][0] < row.indent),
                len(headers) - 1,
            )
            chain = header_chain(headers, index)
        parent = chain[0] if chain else None
        position = len(instances)
        instances.append(
            RowInstance(
                row_id=f"{accession}:{located.statement}:{position}",
                accession=accession,
                statement=located.statement,
                label=row.label,
                parent_header=parent,
                indent=levels[round(row.indent)],
                is_total=row.total,
                is_bold=row.bold,
                position=position,
                cell_texts=row.texts,
                column_periods=row.periods,
                unit_text=row.unit_text,
                table_index=row.table_index,
                source_row=row.source_row,
                column_spans=row.spans,
                column_kinds=row.kinds,
                header_chain=chain,
            )
        )
    return instances


def label_cell_addresses(
    root: etree._Element, table_indices: Iterable[int]
) -> set[tuple[int, int, int]]:
    """(table, row, column) of every label cell in the given tables, by the first-cell rule.

    Read straight from the document, independently of the rows built, so evaluation can tell
    figures in the period columns from numbers written inside a label (share counts, par values).
    """
    tables = document_tables(root)
    addresses: set[tuple[int, int, int]] = set()
    for table_index in table_indices:
        table = tables[table_index]
        grid = table_grid(table)
        for row_index, row in enumerate(_rows(table)):
            label = _label_cell(_row_cells(row, row_index, grid))
            if label is not None:
                addresses.add((table_index, row_index, label.column))
    return addresses


def build_rows(
    clean_html: bytes,
    accession: str,
    tagged_cells: Mapping[int, int] | None = None,
    fiscal_periods: Mapping[int, date] | None = None,
) -> dict[Statement, list[RowInstance]]:
    """Locate the three statements in a clean document and build their rows.

    tagged_cells is the optional {table index: tagged-cell count} map for statement location
    (BC6), computed by the caller outside this package. fiscal_periods is the optional
    {fiscal year: period end} of the filing and the years before it, from the evidence store
    (submissions metadata, not tags; 8.5): it dates only bare-year columns the document leaves
    undated. A statement that cannot be located is absent from the result (an evidence gap for
    the caller), never an empty placeholder.
    """
    root = parse_document(clean_html)
    tables = document_tables(root)
    preceding = preceding_texts(root)
    located_statements = locate_statements(root, tagged_cells)
    filing_unit = filing_unit_sentence(root, item_8_start) if (
        item_8_start := _item_8_position(root, located_statements)
    ) is not None else None
    return {
        statement: statement_rows(
            root, located, accession, tables, preceding, fiscal_periods, filing_unit
        )
        for statement, located in located_statements.items()
    }


def _item_8_position(
    root: etree._Element, located: Mapping[Statement, StatementTables]
) -> int | None:
    """Table position where Item 8 starts (BC12): the Item 8 heading, or, when no heading is
    found outside tables, the first located statement table (the face statements are Item 8)."""
    start = item_8_table_start(root)
    if start is not None:
        return start
    firsts = [l.table_indices[0] for l in located.values() if l.table_indices]
    return min(firsts) if firsts else None
