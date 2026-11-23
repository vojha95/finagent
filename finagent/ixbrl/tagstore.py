"""Tag store: one TagFact per ix:nonFraction, with its table cell address (blueprint 8.2, 13)."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

from finagent.ixbrl.contexts import Context, TagParseError, parse_contexts, parse_units

IX = "http://www.xbrl.org/2013/inlineXBRL"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

# Transform names (local part; the prefix depends on the filing's namespace declarations).
_DECIMAL_FORMATS = {None, "num-dot-decimal", "numdotdecimal"}
_ZERO_FORMATS = {"fixed-zero", "zerodash"}
_WORD_FORMATS = {"numwordsen"}

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# Whole-text zero words under ixt-sec:numwordsen; "nil" is a reported zero, not xsi:nil.
_ZERO_WORDS = {"no", "none", "nil", "zero"}
# Scale words; within one number they must appear largest first.
_SCALES = {"trillion": 10**12, "billion": 10**9, "million": 10**6, "thousand": 10**3}

# No format: the xs:decimal lexical form. A leading "+" is allowed and the integer part may be
# omitted (".96"); "-" is not, because Inline XBRL carries negatives in sign="-".
_XS_DECIMAL = re.compile(r"\+?(\d+(\.\d+)?|\.\d+)")
# ixt:num-dot-decimal: dot decimal separator, integer part optional (".9"), no sign.
_NUM_DOT_DECIMAL = re.compile(r"\d+(\.\d+)?|\.\d+")
# A cell is numeric (not a row label) if it holds only digits, currency and number punctuation.
_NUMERIC_CELL = re.compile(r"[\d$(),.%\s—–\-]*")


@dataclass(frozen=True)
class CellAddress:
    table_index: int
    row_index: int
    column_index: int
    row_label: str | None


@dataclass(frozen=True)
class TagFact:
    accession: str
    concept: str
    context_id: str
    start: date | None  # None for instant contexts
    end: date
    dimensions: dict[str, str]
    unit: str
    scale: int
    sign: int  # +1, or -1 when the fact carries sign="-"
    amount: Decimal | None  # None only for xsi:nil facts; never 0 for a missing value
    cell_address: CellAddress | None  # None when the fact is not inside a table
    format: str | None = None
    decimals: str | None = None
    fact_id: str | None = None
    is_nil: bool = False


def _format_name(fmt: str | None) -> str | None:
    return fmt.split(":")[-1] if fmt else None


def _normalise(text: str) -> str:
    return " ".join(text.replace(" ", " ").split())


def _parse_word_group(words: list[str], text: str) -> int:
    """English words for 0-999, e.g. ['three', 'hundred', 'twenty', 'one'] -> 321."""
    total = 0
    if len(words) >= 2 and words[1] == "hundred":
        if not 0 < _ONES.get(words[0], 0) < 10:
            raise TagParseError(f"Cannot read number words: {text!r}")
        total, words = _ONES[words[0]] * 100, words[2:]
        if not words:
            return total
    if len(words) == 1 and words[0] in _ONES and not (total and words[0] == "zero"):
        return total + _ONES[words[0]]
    if len(words) == 1 and words[0] in _TENS:
        return total + _TENS[words[0]]
    if len(words) == 2 and words[0] in _TENS and 0 < _ONES.get(words[1], 0) < 10:
        return total + _TENS[words[0]] + _ONES[words[1]]
    raise TagParseError(f"Cannot read number words: {text!r}")


def _parse_words(text: str) -> int:
    """ixt-sec:numwordsen: an English cardinal ('two million', 'twenty-one') or a zero word."""
    words = re.split(r"[\s\-]+", text.strip().lower())
    words = [w for w in words if w and w != "and"]
    if len(words) == 1 and words[0] in _ZERO_WORDS:
        return 0
    total, group, last_scale = 0, [], None
    for word in words:
        if word not in _SCALES:
            group.append(word)
            continue
        scale = _SCALES[word]
        if not group or (last_scale is not None and scale >= last_scale):
            raise TagParseError(f"Cannot read number words: {text!r}")
        multiplier = _parse_word_group(group, text)
        if multiplier == 0:
            raise TagParseError(f"Cannot read number words: {text!r}")
        total, group, last_scale = total + multiplier * scale, [], scale
    if group:
        total += _parse_word_group(group, text)
    elif last_scale is None:
        raise TagParseError(f"Cannot read number words: {text!r}")
    return total


def parse_amount(text: str, fmt: str | None, scale: int, sign: int) -> Decimal:
    """Displayed text -> exact amount: number x 10^scale x sign. Parentheses play no part."""
    name = _format_name(fmt)
    if name in _ZERO_FORMATS:
        number = Decimal(0)
    elif name in _WORD_FORMATS:
        number = Decimal(_parse_words(text))
    elif name in _DECIMAL_FORMATS:
        cleaned = re.sub(r"[,\s ]", "", text)
        pattern = _XS_DECIMAL if name is None else _NUM_DOT_DECIMAL
        if not pattern.fullmatch(cleaned):
            raise TagParseError(f"Cannot read number {text!r} with format {fmt}")
        try:
            number = Decimal(cleaned)
        except InvalidOperation as exc:  # pragma: no cover - regex already guards
            raise TagParseError(f"Cannot read number {text!r}") from exc
    else:
        raise TagParseError(f"Unsupported number format: {fmt}")
    if sign not in (1, -1):
        raise TagParseError(f"Sign must be +1 or -1, got {sign}")
    return number.scaleb(scale) * sign


def _local(element: etree._Element) -> str:
    tag = element.tag
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _cells(row: etree._Element) -> list[etree._Element]:
    return [c for c in row if _local(c) in ("td", "th")]


def row_label(row: etree._Element) -> str | None:
    """First-cell rule: the first cell whose text is not purely numeric."""
    for cell in _cells(row):
        text = _normalise("".join(cell.itertext()))
        if text and not _NUMERIC_CELL.fullmatch(text):
            return text
    return None


def _span(cell: etree._Element, name: str) -> int:
    try:
        return max(1, int(cell.get(name, "1")))
    except ValueError:
        return 1


def table_grid(table: etree._Element) -> dict[etree._Element, tuple[int, int]]:
    """Map each cell to (row index, column index), expanding colspan and rowspan."""
    rows = [r for r in table.iter() if _local(r) == "tr"]
    occupied: set[tuple[int, int]] = set()
    positions: dict[etree._Element, tuple[int, int]] = {}
    for row_index, row in enumerate(rows):
        column = 0
        for cell in _cells(row):
            while (row_index, column) in occupied:
                column += 1
            positions[cell] = (row_index, column)
            for r in range(row_index, row_index + _span(cell, "rowspan")):
                for c in range(column, column + _span(cell, "colspan")):
                    occupied.add((r, c))
            column += _span(cell, "colspan")
    return positions


class _CellLocator:
    """Resolves an element to its table cell address; tables are indexed once, in document order."""

    def __init__(self, root: etree._Element) -> None:
        self._tables = {t: i for i, t in enumerate(e for e in root.iter() if _local(e) == "table")}
        self._grids: dict[etree._Element, dict[etree._Element, tuple[int, int]]] = {}

    def address(self, element: etree._Element) -> CellAddress | None:
        cell = row = table = None
        for ancestor in element.iterancestors():
            name = _local(ancestor)
            if cell is None and name in ("td", "th"):
                cell = ancestor
            elif cell is not None and row is None and name == "tr":
                row = ancestor
            elif row is not None and name == "table":
                table = ancestor
                break
        if table is None:
            return None
        grid = self._grids.setdefault(table, table_grid(table))
        row_index, column_index = grid[cell]
        return CellAddress(
            table_index=self._tables[table],
            row_index=row_index,
            column_index=column_index,
            row_label=row_label(row),
        )


def parse_document(document: bytes) -> etree._Element:
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(document, parser)
    except etree.XMLSyntaxError as exc:
        raise TagParseError(f"Document is not well-formed XHTML: {exc}") from exc


def _fact(
    element: etree._Element,
    accession: str,
    contexts: dict[str, Context],
    units: dict[str, str],
    locator: _CellLocator,
) -> TagFact:
    fact_id = element.get("id")
    concept = element.get("name")
    context_ref = element.get("contextRef")
    unit_ref = element.get("unitRef")
    if not concept:
        raise TagParseError(f"Fact {fact_id} has no concept name")
    if context_ref not in contexts:
        raise TagParseError(f"Fact {fact_id} refers to unknown context {context_ref}")
    if unit_ref not in units:
        raise TagParseError(f"Fact {fact_id} refers to unknown unit {unit_ref}")
    context = contexts[context_ref]
    is_nil = element.get(XSI_NIL) in ("true", "1")
    scale = int(element.get("scale", "0"))
    sign = -1 if element.get("sign") == "-" else 1
    fmt = element.get("format")
    amount = None if is_nil else parse_amount("".join(element.itertext()), fmt, scale, sign)
    return TagFact(
        accession=accession,
        concept=concept,
        context_id=context_ref,
        start=context.start,
        end=context.end,
        dimensions=dict(context.dimensions),
        unit=units[unit_ref],
        scale=scale,
        sign=sign,
        amount=amount,
        cell_address=locator.address(element),
        format=fmt,
        decimals=element.get("decimals"),
        fact_id=fact_id,
        is_nil=is_nil,
    )


def build_tag_store(document: bytes, accession: str) -> list[TagFact]:
    """One TagFact per ix:nonFraction in document order (nested facts each get a record)."""
    root = parse_document(document)
    contexts = parse_contexts(root)
    units = parse_units(root)
    locator = _CellLocator(root)
    return [
        _fact(element, accession, contexts, units, locator)
        for element in root.iter(f"{{{IX}}}nonFraction")
    ]
