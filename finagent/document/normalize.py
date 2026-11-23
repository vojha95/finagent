"""Value normaliser: displayed figures -> exact amounts in canonical sign (blueprint 8.5).

Scale comes from the table unit line (per-share and share-count rows exempt); parentheses mean
negative as displayed; a dash is 0 only because the filer displays a dash for zero; a blank is
a gap, never 0 (rule 4). Items whose standard concept is positive (Appendix A "+") are stored
as magnitudes, so the two channels compare like with like; the flip is recorded.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from finagent.config import SignRule
from finagent.records import RowInstance

_SCALES = {"thousands": 3, "millions": 6, "billions": 9}
_UNIT_WORD = re.compile(r"\b(thousands|millions|billions)\b", re.IGNORECASE)
_DASHES = {"—", "–", "-", "— —"}
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_PER_SHARE = re.compile(r"per (common )?share|earnings per|\beps\b", re.IGNORECASE)
_SHARE_COUNT = re.compile(r"\bshares\b|weighted[- ]average", re.IGNORECASE)

# Reason codes for values that could not be read.
BLANK = "BLANK"
UNREADABLE = "UNREADABLE"
NO_UNIT_LINE = "NO_UNIT_LINE"
NOT_AN_AMOUNT = "NOT_AN_AMOUNT"  # a percentage sub-column (BC11), never read as a value


@dataclass(frozen=True)
class DisplayedFigure:
    """A cell's text read as displayed: number (unscaled), sign and whether it was a dash."""

    number: Decimal | None
    negative: bool
    dash: bool


@dataclass(frozen=True)
class NormalisedValue:
    row_id: str
    period_end: date | None
    raw_text: str
    value: Decimal | None  # None is a gap (blank or unreadable), never 0
    reason: str | None  # BLANK / UNREADABLE / NO_UNIT_LINE / NOT_AN_AMOUNT when value is None
    scale: int
    sign_flipped: bool  # displayed negative, stored as a magnitude (positive-sign item)


def unit_scale(unit_text: str | None) -> int | None:
    """Power of ten from the unit line, e.g. "(in millions)" -> 6; None when absent."""
    match = _UNIT_WORD.search(unit_text or "")
    return _SCALES[match.group(1).lower()] if match else None


def is_scale_exempt(row: RowInstance) -> bool:
    """Per-share and share-count rows are not in the table's monetary unit (8.5)."""
    text = f"{row.parent_header or ''} {row.label}"
    return bool(_PER_SHARE.search(text) or _SHARE_COUNT.search(text))


def read_display(text: str) -> DisplayedFigure | None:
    """'$ (1,234 )' -> 1234, negative. '—' -> dash. Returns None for text that is not a figure."""
    cleaned = " ".join(text.replace("$", " ").replace(" ", " ").split())
    if not cleaned:
        return None
    if cleaned in _DASHES:
        return DisplayedFigure(Decimal(0), negative=False, dash=True)
    negative = cleaned.startswith("(") or cleaned.startswith("-") or cleaned.startswith("−")
    numbers = _NUMBER.findall(cleaned)
    residue = _NUMBER.sub("", cleaned).replace("(", "").replace(")", "").replace("-", "")
    if len(numbers) != 1 or residue.replace("%", "").replace("−", "").strip():
        return None
    try:
        number = Decimal(numbers[0].replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the regex already guards
        return None
    return DisplayedFigure(number, negative=negative, dash=False)


def normalise_cell(
    row: RowInstance, column: int, sign: SignRule = "either"
) -> NormalisedValue:
    """One period column of one row, as an exact amount in the item's canonical sign."""
    raw = row.cell_texts[column]
    period = row.column_periods[column]
    if not row.is_amount_column(column):
        return NormalisedValue(row.row_id, period, raw, None, NOT_AN_AMOUNT, 0, False)
    exempt = is_scale_exempt(row)
    scale = 0 if exempt else unit_scale(row.unit_text)
    if not raw.strip():
        return NormalisedValue(row.row_id, period, raw, None, BLANK, scale or 0, False)
    figure = read_display(raw)
    if figure is None:
        return NormalisedValue(row.row_id, period, raw, None, UNREADABLE, scale or 0, False)
    if scale is None:
        return NormalisedValue(row.row_id, period, raw, None, NO_UNIT_LINE, 0, False)
    amount = figure.number.scaleb(scale)
    flipped = figure.negative and sign == "positive"
    if figure.negative and not flipped:
        amount = -amount
    return NormalisedValue(row.row_id, period, raw, amount, None, scale, flipped)


def normalise_row(row: RowInstance, sign: SignRule = "either") -> list[NormalisedValue]:
    return [normalise_cell(row, column, sign) for column in range(len(row.cell_texts))]


def value_for_period(
    row: RowInstance, period_end: date, sign: SignRule = "either"
) -> NormalisedValue | None:
    """The row's value for the filing's own period; None when no column has that end date."""
    columns = [
        c for c, p in enumerate(row.column_periods) if p == period_end and row.is_amount_column(c)
    ]
    if not columns:
        return None
    return normalise_cell(row, columns[0], sign)
