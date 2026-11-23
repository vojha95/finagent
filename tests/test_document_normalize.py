from datetime import date
from decimal import Decimal

import pytest

from finagent.document.normalize import (
    BLANK,
    NO_UNIT_LINE,
    NOT_AN_AMOUNT,
    UNREADABLE,
    is_scale_exempt,
    normalise_cell,
    normalise_row,
    read_display,
    unit_scale,
    value_for_period,
)
from finagent.records import RowInstance

P1, P2 = date(2024, 12, 31), date(2025, 12, 31)


def _row(label="Cash", texts=("$ 10", "12"), unit="(in millions)", parent=None) -> RowInstance:
    return RowInstance(
        row_id="a:BS:0", accession="a", statement="BS", label=label, parent_header=parent,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=texts,
        column_periods=(P1, P2), unit_text=unit, table_index=0, source_row=0,
        column_spans=((1, 2), (3, 4)),
    )


@pytest.mark.parametrize(
    ("text", "number", "negative", "dash"),
    [
        ("$ 78,779", "78779", False, False),
        ("(1,234 )", "1234", True, False),
        ("$ (7,837)", "7837", True, False),
        ("18.00", "18.00", False, False),
        ("—", "0", False, True),
        ("12%", "12", False, False),
    ],
)
def test_read_display(text, number, negative, dash):
    figure = read_display(text)
    assert figure.number == Decimal(number)
    assert (figure.negative, figure.dash) == (negative, dash)


@pytest.mark.parametrize("text", ["", "n/a", "1,234 and 5", "Note 7"])
def test_read_display_refuses_non_figures(text):
    assert read_display(text) is None


def test_unit_scale():
    assert unit_scale("(in millions, except per share data)") == 6
    assert unit_scale("(In thousands)") == 3
    assert unit_scale(None) is None


def test_scale_exempt_rows():
    assert is_scale_exempt(_row("Diluted earnings per share"))
    assert is_scale_exempt(_row("Diluted", parent="Weighted-average shares used in computation"))
    assert not is_scale_exempt(_row("Cash and cash equivalents"))


def test_normalise_scales_and_keeps_displayed_sign():
    values = normalise_row(_row(texts=("(5 )", "12")))
    assert [v.value for v in values] == [Decimal("-5E+6"), Decimal("12E+6")]
    assert values[0].period_end == P1 and values[0].scale == 6


def test_positive_items_are_stored_as_magnitudes():
    capex = normalise_cell(_row("Purchases of property and equipment", ("(131,819)", "1")), 0,
                           "positive")
    assert capex.value == Decimal("131819E+6") and capex.sign_flipped


def test_blank_is_a_gap_and_dash_is_zero():
    blank, dash = normalise_row(_row(texts=("", "—")))
    assert blank.value is None and blank.reason == BLANK  # missing is never zero
    assert dash.value == 0 and dash.reason is None


def test_unreadable_and_missing_unit_line_are_gaps():
    assert normalise_cell(_row(texts=("see note", "1")), 0).reason == UNREADABLE
    assert normalise_cell(_row(unit=None), 0).reason == NO_UNIT_LINE


def test_per_share_values_are_not_scaled():
    eps = normalise_cell(_row("Diluted earnings per share", ("$ 17.95", "1")), 0)
    assert eps.value == Decimal("17.95") and eps.scale == 0


def test_value_for_period_picks_the_filing_column():
    row = _row(texts=("10", "12"))
    assert value_for_period(row, P2).value == Decimal("12E+6")
    assert value_for_period(row, date(2023, 12, 31)) is None


def test_percent_column_is_never_read_as_an_amount():
    row = RowInstance(
        row_id="a:IS:0", accession="a", statement="IS", label="Net sales", parent_header=None,
        indent=0, is_total=False, is_bold=False, position=0,
        cell_texts=("$ 86,286", "100.00 %", "$ 83,674", "100.00 %"),
        column_periods=(P2, None, P1, None), unit_text="(in millions)", table_index=0,
        source_row=0, column_spans=((1, 2), (3, 3), (4, 5), (6, 6)),
        column_kinds=("amount", "percent", "amount", "percent"),
    )
    values = normalise_row(row)
    assert [v.reason for v in values] == [None, NOT_AN_AMOUNT, None, NOT_AN_AMOUNT]
    assert values[1].value is None and values[0].value == Decimal("86286000000")
    assert value_for_period(row, P2).value == Decimal("86286000000")
    assert _row().is_amount_column(0)  # no kinds given: every column is an amount column
