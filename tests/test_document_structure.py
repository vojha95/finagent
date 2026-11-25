import math
from datetime import date
from decimal import Decimal

from finagent.document.structure import (
    FEATURES,
    displayed_figures,
    pair_span,
    row_features,
    statement_features,
    subtotal_span,
    total_of_subtotals,
)
from finagent.records import RowInstance

D = Decimal


def _row(label, cells, position, statement="IS", accession="a", is_total=False, indent=0,
         kinds=()) -> RowInstance:
    return RowInstance(
        row_id=f"{accession}:{statement}:{position}", accession=accession, statement=statement,
        label=label, parent_header=None, indent=indent, is_total=is_total, is_bold=False,
        position=position, cell_texts=tuple(cells),
        column_periods=tuple(date(2025 - i, 12, 31) for i in range(len(cells))),
        unit_text="(in millions)", table_index=0, source_row=position,
        column_spans=tuple((i + 1, i + 1) for i in range(len(cells))), column_kinds=kinds,
    )


def _figures(*rows):
    return [[None if v is None else D(v) for v in r] for r in rows]


def test_displayed_figures_signs_dashes_blanks_and_percent_columns():
    row = _row("x", ["$ 1,000", "(200 )", "—", "", "12.5%"], 0,
               kinds=("amount", "amount", "amount", "amount", "percent"))
    assert displayed_figures(row) == [D(1000), D(-200), D(0), None]


def test_subtotal_sum_and_difference():
    figures = _figures([10, 20], [5, 6], [15, 26], [4, 5], [11, 21])
    assert subtotal_span(figures, 2) == 2  # 10 + 5, 20 + 6
    assert subtotal_span(figures, 4, difference=True) == 2  # 15 - 4, 26 - 5
    assert subtotal_span(figures, 1) == 0  # nothing above to add


def test_subtotal_needs_every_column_and_two_non_zero_parts():
    assert subtotal_span(_figures([10, 20], [5, 6], [15, 99]), 2) == 0  # second column fails
    assert subtotal_span(_figures([10, 1], [0, 2], [10, 3]), 2) == 0  # one non-zero part first
    assert subtotal_span(_figures([10, None], [5, 6], [15, None]), 2) == 2  # unshown column skipped


def test_subtotal_tolerates_display_rounding():
    # half a displayed unit per figure: 3 figures at one decimal -> 0.15
    assert subtotal_span(_figures(["1.2"], ["2.4"], ["3.7"]), 2) == 2
    assert subtotal_span(_figures(["1.2"], ["2.4"], ["3.8"]), 2) == 0


def test_pair_span_skips_rows_in_between():
    # revenue, cost components, cost total, gross profit = revenue - cost total
    figures = _figures([100], [30], [10], [40], [60])
    assert pair_span(figures, 4) == 4
    assert pair_span(figures, 1) == 0


def test_total_of_subtotals_adds_the_rows_after_the_last():
    # operating (2), investing (5) and financing (8) subtotals, an FX row, the net change
    figures = _figures([10], [5], [15], [-4], [-2], [-6], [-3], [-1], [-4], [1], [6])
    assert total_of_subtotals(figures, 10, [2, 5, 8]) == 3  # 15 - 6 - 4 + 1
    assert total_of_subtotals(figures, 10, [2]) == 0


def test_statement_features_totals_magnitude_and_nan_for_missing():
    rows = [
        _row("Net sales", ["100", "90"], 0),
        _row("Cost of sales", ["(60 )", "(50 )"], 1, indent=1),
        _row("Gross margin", ["40", "40"], 2, is_total=True),
        _row("Weighted-average shares outstanding", ["1,000", "1,000"], 3),
        _row("Note", ["", ""], 4),
    ]
    features = statement_features(rows)
    gross, shares, blank = features[2], features[3], features[4]
    assert gross["subtotal_sum"] == 1.0 and gross["is_total"] == 1.0
    assert gross["log_share_of_max"] == math.log10(0.4)
    assert features[0]["log_share_of_max"] == 0.0 and features[1]["negative"] == 1.0
    assert shares["scale_exempt"] == 1.0 and math.isnan(shares["log_share_of_max"])
    assert blank["has_figure"] == 0.0 and math.isnan(blank["negative"])
    assert features[4]["relative_position"] == 1.0 and features[0]["statement_is"] == 1.0


def test_row_features_groups_by_filing_and_statement():
    rows = [_row("Revenue", ["10"], 0, accession="a"), _row("Revenue", ["1,000"], 0,
                                                            accession="b"),
            _row("Cash", ["5"], 0, statement="BS", accession="a"),
            _row("Other", ["1"], 1, accession="a")]
    vectors = row_features(rows)
    share = FEATURES.index("log_share_of_max")
    assert all(len(v) == len(FEATURES) for v in vectors)
    assert vectors[0][share] == vectors[1][share] == vectors[2][share] == 0.0  # each its own max
    assert vectors[3][share] == -1.0
