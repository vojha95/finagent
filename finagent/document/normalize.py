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
# A per-share value: "per share", "per common share", "per-share", "earnings per", "EPS".
_PER_SHARE = re.compile(
    r"\bper[- ](?:common |ordinary |basic |diluted )?share(?![a-z])|\bearnings per\b|\beps\b",
    re.IGNORECASE,
)
# A share count: the line itself counts shares ("Weighted-average shares outstanding", "Shares
# used in computing ...", "Average number of common shares", "Common shares outstanding").
_SHARE_COUNT = re.compile(
    r"\baverage\b.*\bshares(?![a-z])|\bnumber of\b.*\bshares(?![a-z])"
    r"|^\W*(?:basic |diluted )?shares(?![a-z])|\bshares\b.*\b(?:outstanding|used)(?![a-z])",
    re.IGNORECASE,
)
# A rate quoted on a dollar line ("Cash dividends ($0.83 per share)", "$2.20 per share"), a
# parenthetical, or a dollar line's description ("$1 par value", "at cost") is not the line.
_PARENTHETICAL = re.compile(r"\([^()]*\)")
_DOLLAR_RATE = re.compile(r"\$\s*[\d.,]+\s*per[- ]\w*\s*share\b", re.IGNORECASE)
_DOLLAR_LINE = re.compile(r"\$|\bpar value\b|\bstated value\b|\bat cost\b|\bauthorized\b",
                          re.IGNORECASE)
# Footnote markers glued to a word ("Per Share1") end the word; (?![a-z]) is used, not \b.
_HEAD_END = re.compile(r"[,;(]")
# A header that is a unit line ("(In millions, except per share amounts)") names no line item.
_UNIT_HEADER = re.compile(r"\b(?:thousands|millions|billions)\b", re.IGNORECASE)

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


def _line_text(text: str) -> str:
    """The wording that names the line: parentheticals and quoted dollar rates removed."""
    return _DOLLAR_RATE.sub(" ", _PARENTHETICAL.sub(" ", text))


def _head(text: str) -> str:
    """The line's noun phrase: the text before its first comma, semicolon or parenthesis."""
    return _HEAD_END.split(text, maxsplit=1)[0]


def _is_unit_header(text: str) -> bool:
    """A header that is only a unit line ("(In millions, except per share amounts)")."""
    line = _line_text(text)
    return not line.strip(" :") or bool(_UNIT_HEADER.search(line))


def is_per_share_text(text: str) -> bool:
    """True when the wording names a per-share value ("Diluted earnings per share")."""
    return bool(_PER_SHARE.search(_line_text(text)))


def is_share_count_text(text: str) -> bool:
    """True when the line itself is a share count, not a dollar line that mentions shares.

    "Weighted-average shares outstanding" is; "Common stock, $1 par value; 2,000 shares
    authorized", "Treasury shares, at cost" and "Repurchases of common shares" are not.
    """
    if _DOLLAR_LINE.search(text):
        return False
    return bool(_SHARE_COUNT.search(_head(text)))


def is_scale_exempt(row: RowInstance) -> bool:
    """Per-share and share-count rows are not in the table's monetary unit (8.5, #53, #64).

    Per-share: the wording is in the label, the parent header or any enclosing header up the
    chain (CVX "- Basic" under "Net Income ..." under "Per Share of Common Stock"). Share count:
    the label or its parent header is itself a share count ("Diluted" under "Weighted-average
    shares"), unless that parent is a dollar line ("Common stock at stated value:"). Headers
    that are unit lines ("in millions, except per share") are ignored.
    """
    headers = row.header_chain or ((row.parent_header,) if row.parent_header else ())
    headers = tuple(h for h in headers if not _is_unit_header(h))
    if any(is_per_share_text(t) for t in (row.label, *headers)):
        return True
    parent = headers[:1]
    if parent and _DOLLAR_LINE.search(parent[0]):
        return False  # a child of "Common stock, $0.01 par value:" shows dollars
    return any(is_share_count_text(t) for t in (row.label, *parent))


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
