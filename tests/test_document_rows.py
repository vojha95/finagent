import dataclasses
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from finagent.document import (
    NO_TABLES,
    STATEMENT_NOT_LOCATED,
    STATEMENTS_INCORPORATED_BY_REFERENCE,
    build_rows,
    cell_coverage,
    covered_cells,
    gap_reason,
    incorporated_by_reference,
    indent_points,
    is_period_caption,
    item_8_table_start,
    label_cell_addresses,
    locate_statements,
    parse_style,
    preceding_texts,
    stated_dates,
    title_dates,
    title_month_day,
    to_points,
)
from finagent.config import HELD_OUT, load_universe
from finagent.evidence import EvidenceStore
from finagent.evidence.store import FilingRecord
from finagent.evidence.integrity import read_snapshot
from finagent.filing_rows import period_disagreements, read_filing_rows
from finagent.ixbrl import build_tag_store, parse_document
from finagent.records import RowInstance

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "evidence"
XHTML = 'xmlns="http://www.w3.org/1999/xhtml"'


# ---------- synthetic documents ----------

def _doc(body: str) -> bytes:
    return f"<html {XHTML}><body>{body}</body></html>".encode()


def _tr(label: str, *cells: str, label_style: str = "", row_style: str = "") -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f'<tr style="{row_style}"><td style="{label_style}">{label}</td>{tds}</tr>'


BALANCE_SHEET = (
    "<p>CONSOLIDATED BALANCE SHEETS (in millions)</p>"
    "<table>"
    '<tr><td></td><td colspan="4">December 31,</td></tr>'
    '<tr><td></td><td colspan="2">2024</td><td colspan="2">2025</td></tr>'
    + _tr("Current assets:", "", "", "", "", label_style="padding-left:1pt")
    + _tr("Cash", "$", "10", "$", "12", label_style="padding:2px 1pt 2px 19pt")
    + _tr("Receivables", "", "(5", ")", "7", label_style="padding-left:19pt")
    + _tr("Total current assets", "", "5", "", "19", label_style="padding-left:28pt")
    + _tr("Goodwill", "", "3", "", "—", label_style="padding-left:1pt")
    + _tr("Total assets", "", "8", "", "19", label_style="padding-left:37pt")
    + _tr("Total current liabilities", "", "1", "", "1")
    + _tr("Stockholders’ equity:", "", "", "", "")
    + _tr("Retained earnings", "", "7", "", "18", label_style="padding-left:19pt")
    + _tr("Total liabilities and stockholders’ equity", "", "8", "", "19")
    + "</table>"
)


def _rows(body: str, statement: str = "BS") -> list[RowInstance]:
    return build_rows(_doc(body), "0000000000-25-000001")[statement]


def test_parse_style_and_points():
    style = parse_style("Padding:2px 1pt; FONT-WEIGHT:700;;text-indent: -2.5%")
    assert style == {"padding": "2px 1pt", "font-weight": "700", "text-indent": "-2.5%"}
    assert to_points("19pt") == 19 and to_points("8px") == 6 and to_points("0.5in") == 36
    assert to_points("junk") == 0


def test_indent_points_reads_padding_shorthand_margin_and_text_indent():
    root = parse_document(_doc(
        '<table><tr><td style="padding:2px 1pt 2px 19pt"><p style="margin-left:2pt;'
        'text-indent:3pt"><span>Cash</span></p></td></tr></table>'
    ))
    cell = next(e for e in root.iter() if e.tag.endswith("td"))
    assert indent_points(cell) == 24


def test_rows_labels_periods_and_figures():
    rows = _rows(BALANCE_SHEET)
    assert [r.label for r in rows][:3] == ["Cash", "Receivables", "Total current assets"]
    assert rows[0].column_periods == (date(2024, 12, 31), date(2025, 12, 31))
    assert rows[0].cell_texts == ("$ 10", "$ 12")
    assert rows[1].cell_texts == ("(5 )", "7")  # closing bracket in its own cell kept
    goodwill = next(r for r in rows if r.label == "Goodwill")
    assert goodwill.cell_texts == ("3", "—")  # a dash is kept as text, never turned into 0
    assert all(r.unit_text == "(in millions)" for r in rows)
    assert [r.position for r in rows] == list(range(len(rows)))
    assert rows[0].row_id == "0000000000-25-000001:BS:0"


def test_parent_headers_indent_and_totals():
    rows = {r.label: r for r in _rows(BALANCE_SHEET)}
    assert rows["Cash"].parent_header == "Current assets:"
    assert rows["Total current assets"].parent_header == "Current assets:"
    assert rows["Total current assets"].is_total and not rows["Cash"].is_total
    # "Total current assets" closed its section, so Goodwill is no longer under it.
    assert rows["Goodwill"].parent_header != "Current assets:"
    assert rows["Retained earnings"].parent_header == "Stockholders’ equity:"
    assert rows["Cash"].indent > rows["Goodwill"].indent
    assert rows["Total assets"].indent > rows["Total current assets"].indent


def test_subtotal_naming_another_item_does_not_close_section():
    body = BALANCE_SHEET.replace(
        _tr("Total current assets", "", "5", "", "19", label_style="padding-left:28pt"),
        _tr("Total cash and receivables", "", "5", "", "19", label_style="padding-left:28pt")
        + _tr("Inventory", "", "1", "", "1", label_style="padding-left:19pt"),
    )
    rows = {r.label: r for r in _rows(body)}
    assert rows["Inventory"].parent_header == "Current assets:"


def test_spacer_row_rule_marks_next_row_total_and_collapsed_rows_skipped():
    body = BALANCE_SHEET.replace(
        _tr("Total assets", "", "8", "", "19", label_style="padding-left:37pt"),
        '<tr style="visibility:collapse"><td>Hidden</td><td>9</td></tr>'
        '<tr><td style="border-bottom:1pt solid"></td><td></td></tr>'
        + _tr("Assets in all", "", "8", "", "19"),
    )
    rows = {r.label: r for r in _rows(body)}
    assert "Hidden" not in rows
    assert rows["Assets in all"].is_total


def test_full_date_headers_for_52_53_week_years():
    body = (
        "<table>"
        '<tr><td></td><td>February 1, 2025</td><td>February 3, 2024</td></tr>'
        + _tr("Total current assets", "5", "4") + _tr("Total assets", "9", "8")
        + _tr("Total current liabilities", "1", "1")
        + _tr("Shareholders’ equity", "8", "7")
        + _tr("Total liabilities and shareholders’ equity", "9", "8")
        + "</table>"
    )
    rows = _rows(body)
    assert rows[0].column_periods == (date(2025, 2, 1), date(2024, 2, 3))


def test_year_without_month_day_gives_unknown_period():
    body = BALANCE_SHEET.replace("December 31,", "As at").replace(
        "CONSOLIDATED BALANCE SHEETS", "BALANCE SHEETS"
    )
    rows = _rows(body)
    assert rows[0].column_periods == (None, None)  # an unknown date is a gap, not a guess


def test_split_statement_merges_only_with_identical_headers():
    split_at = _tr("Retained earnings", "", "7", "", "18", label_style="padding-left:19pt")
    first, second = BALANCE_SHEET.split(split_at)
    header = '<tr><td></td><td colspan="2">2024</td><td colspan="2">2025</td></tr>'
    # The first table lacks the closing line, so the next table continues the statement.
    continued = first + "</table><p>December 31</p><table>" + header + split_at + second
    rows = _rows(continued)
    assert rows[-1].label == "Total liabilities and stockholders’ equity"
    assert len({r.table_index for r in rows}) == 2

    other_header = header.replace("2024", "2022").replace("2025", "2023")
    first_table_only = _rows(continued.replace(
        "</table><p>December 31</p><table>" + header,
        "</table><p>December 31</p><table>" + other_header,
    ))
    assert len({r.table_index for r in first_table_only}) == 1


def test_statement_absent_when_no_table_matches():
    assert build_rows(_doc("<table><tr><td>Hello</td><td>1</td></tr></table>"), "a") == {}


def test_item_8_heading_outside_tables():
    root = parse_document(_doc(
        "<table><tr><td>Item 8. Financial Statements</td></tr></table>"
        "<p>Item 8. Financial Statements and Supplementary Data</p><table><tr><td/></tr></table>"
    ))
    assert item_8_table_start(root) == 1


def test_preceding_texts_and_label_cells():
    root = parse_document(_doc(BALANCE_SHEET))
    assert "(in millions)" in preceding_texts(root)[0]
    labels = label_cell_addresses(root, [0])
    assert (0, 3, 0) in labels and (0, 0, 0) not in labels  # "Cash" row; header row has no label


def test_cell_coverage_counts_tagged_cells_in_period_columns():
    rows = _rows(BALANCE_SHEET)
    cash = rows[0]
    tagged = [(0, cash.source_row, 2), (0, cash.source_row, 4), (0, cash.source_row, 0), (9, 0, 0)]
    coverage = cell_coverage(rows, tagged)
    assert (coverage.covered, coverage.total) == (2, 3)  # (9, 0, 0) is not a statement table
    assert coverage.missed == ((0, cash.source_row, 0),)
    assert (0, cash.source_row, 1) in covered_cells(rows)


def test_document_package_never_reads_tags():
    """Rule 7: the document channel may reuse HTML helpers but never the tag store or channel."""
    forbidden = re.compile(r"build_tag_store|TagFact|finagent\.tags|ix_concept_names|nonFraction")
    for path in (ROOT / "finagent" / "document").glob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name


# ---------- real filings: Phase 1 exit criterion (blueprint 15) ----------

MANIFEST = json.loads((EVIDENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
FILINGS = [
    (ticker, record)
    for ticker in ("AMZN", "MSFT")
    for record in EvidenceStore(EVIDENCE_DIR).load_filings(ticker, date(2026, 12, 31))
]


@lru_cache(maxsize=None)
def _built(document_name: str, accession: str):
    """Rows located with the tagged-cell map, as every real-filing caller does (BC6)."""
    document = read_snapshot(EVIDENCE_DIR, document_name, MANIFEST)
    filing = read_filing_rows(document, accession)
    return document, filing.clean, filing.rows


@pytest.mark.parametrize(
    ("ticker", "record"), FILINGS, ids=[f"{t}-FY{r.fiscal_year}" for t, r in FILINGS]
)
def test_three_statements_found_and_tagged_cells_mapped(ticker, record):
    document, clean, rows = _built(record.primary_document, record.accession)
    assert set(rows) == {"IS", "BS", "CF"}

    root = parse_document(clean)
    facts = build_tag_store(document, record.accession)
    for statement, statement_rows in rows.items():
        assert statement_rows, statement
        assert record.end in statement_rows[0].column_periods, statement
        assert statement_rows[0].unit_text, statement
        labels = label_cell_addresses(root, {r.table_index for r in statement_rows})
        # Tagged figures in the statement: nil facts and numbers written inside a label
        # (share counts, par values, allowances) are not statement figures.
        tagged = [
            (f.cell_address.table_index, f.cell_address.row_index, f.cell_address.column_index)
            for f in facts
            if f.cell_address is not None and not f.is_nil
        ]
        tagged = [a for a in tagged if a not in labels]
        coverage = cell_coverage(statement_rows, tagged)
        assert coverage.total > 0, statement
        assert coverage.share >= 0.95, (statement, coverage.missed[:5])


def test_amazon_fy2025_balance_sheet_rows():
    record = next(r for t, r in FILINGS if t == "AMZN" and r.fiscal_year == 2025)
    rows = {r.label: r for r in _built(record.primary_document, record.accession)[2]["BS"]}
    total_assets = rows["Total assets"]
    assert total_assets.is_total
    assert total_assets.cell_texts[total_assets.column_periods.index(record.end)] == "$ 818,042"
    assert rows["Cash and cash equivalents"].parent_header == "Current assets:"


# ---------- statement location with a tagged-cell map (BC6) and gap reasons ----------

def _cf_table(*, ending: str = "Cash and cash equivalents at end of year", extra: str = "") -> str:
    return (
        "<table>"
        '<tr><td></td><td>2025</td><td>2024</td></tr>'
        + _tr("Operating activities", "", "")
        + _tr("Net income", "1,200", "1,100")
        + _tr("Net cash provided by operating activities", "2,000", "1,900")
        + _tr("Net cash used in investing activities", "(500)", "(400)")
        + _tr("Net cash used in financing activities", "(900)", "(800)")
        + _tr(ending, "4,000", "3,400")
        + extra
        + "</table>"
    )


CF_SUMMARY = (  # an untagged MD&A summary: all five CF groups and many numbers
    "<table>"
    '<tr><td></td><td>2025</td><td>2024</td></tr>'
    + _tr("Net cash provided by operating activities", "2,000", "1,900")
    + _tr("Net cash used in investing activities", "(500)", "(400)")
    + _tr("Net cash used in financing activities", "(900)", "(800)")
    + _tr("Cash at end of year", "4,000", "3,400")
    + _tr("Capital expenditures", "1,000", "1,200")
    + _tr("Free cash flow", "1,000", "1,700")
    + "</table>"
)


def _locate(body: str, counts=None):
    return locate_statements(parse_document(_doc(body)), counts)


def test_without_map_location_uses_wording_alone():
    located = _locate(CF_SUMMARY + _cf_table())
    assert located["CF"].table_indices == (0,)  # denser untagged table wins on wording alone


def test_map_excludes_untagged_tables():
    located = _locate(CF_SUMMARY + _cf_table(), {1: 12})
    assert located["CF"].table_indices == (1,)
    assert _locate(CF_SUMMARY + _cf_table(), {}) == {}  # nothing tagged: nothing located


def test_map_ties_break_by_position_not_by_tagged_count():
    # Two tagged tables with the same anchor groups: the earlier wins, even with fewer tags
    # (a quarterly table can hold more tagged cells than the statement itself).
    located = _locate(_cf_table() + _cf_table(extra=_tr("Other", "1", "2")), {0: 10, 1: 50})
    assert located["CF"].table_indices == (0,)


def test_map_lowers_threshold_to_three_groups():
    unclassified = (
        "<table>"
        '<tr><td></td><td>2025</td><td>2024</td></tr>'
        + _tr("Total assets", "9,000", "8,000")
        + _tr("Total stockholders’ equity", "4,000", "3,000")
        + _tr("Total liabilities and stockholders’ equity", "9,000", "8,000")
        + "</table>"
    )
    assert "BS" not in _locate(unclassified)
    assert _locate(unclassified, {0: 6})["BS"].anchor_groups == 3


def test_map_locates_split_statement_across_spacer_table():
    first = (
        "<table>"
        '<tr><td></td><td>2025</td><td>2024</td></tr>'
        + _tr("Operating activities", "", "")
        + _tr("Net cash provided by operating activities", "2,000", "1,900")
        + "</table>"
    )
    second = (
        "<table>"
        '<tr><td></td><td>2025</td><td>2024</td></tr>'
        + _tr("Financing activities", "", "")
        + _tr("Proceeds from issuance of debt", "900", "800")
        + _tr("Cash and cash equivalents, end of year", "4,000", "3,400")
        + "</table>"
    )
    body = first + "<table><tr><td></td></tr></table>" + second
    assert "CF" not in _locate(body, {0: 4})  # the second table untagged: not joined
    assert _locate(body, {0: 4, 2: 6})["CF"].table_indices == (0, 2)


def test_continuation_with_map_skips_spacer_and_needs_tags():
    ending = _tr("Cash and cash equivalents at end of year", "4,000", "3,400")
    first, rest = _cf_table().split(ending)
    head = '<tr><td></td><td>2025</td><td>2024</td></tr>'
    body = first + "</table><table><tr><td></td></tr></table><table>" + head + ending + rest
    assert _locate(body, {0: 8, 2: 4})["CF"].table_indices == (0, 2)
    assert _locate(body, {0: 8})["CF"].table_indices == (0,)  # untagged: not a continuation
    assert _locate(body)["CF"].table_indices == (0,)  # wording only: a spacer ends it, as before


def test_general_wording_cash_generated_and_ending_balances():
    body = _cf_table(ending="Cash, cash equivalents and restricted cash, ending balances").replace(
        "Net cash provided by operating activities", "Cash generated by operating activities"
    ).replace("Net cash used in", "Cash used in")
    assert _locate(body)["CF"].anchor_groups == 5


def test_map_index_out_of_range_raises():
    with pytest.raises(ValueError, match="outside"):
        _locate(_cf_table(), {3: 1})


def test_no_tables_is_a_gap_not_a_crash():
    root = parse_document(_doc("<div>Consolidated Statement of Cash Flows</div>"))
    assert locate_statements(root) == {} and locate_statements(root, {}) == {}
    assert build_rows(_doc("<div>no tables</div>"), "a", {}) == {}
    assert gap_reason(root, {}) == NO_TABLES


def test_gap_reasons_from_item_8_wording():
    by_reference = _doc(
        "<p>Item 8. Financial Statements and Supplementary Data: Refer to pages 42 through 116 "
        "of the Annual Report to Stockholders, which are incorporated herein by reference.</p>"
        "<table><tr><td>Schedule</td><td>12</td></tr></table>"
    )
    root = parse_document(by_reference)
    assert incorporated_by_reference(root)
    assert gap_reason(root, {}) == STATEMENTS_INCORPORATED_BY_REFERENCE
    assert gap_reason(root, {"IS": object()}) == STATEMENT_NOT_LOCATED  # some found: not that
    split = parse_document(_doc(
        "<div><span>Item 8. Financial Statements and Supplementary Data:</span></div>"
        "<div>Refer to the Annual Report, which is incorporated herein by reference.</div>"
    ))
    assert incorporated_by_reference(split)  # heading and sentence in separate elements
    later = parse_document(_doc(
        "<p>Item 8. Financial Statements</p><p>Item 9. Changes in accountants</p>"
        "<p>Exhibit 4 is incorporated herein by reference.</p>"
    ))
    assert not incorporated_by_reference(later)  # wording under a later Item does not count
    plain = parse_document(_doc("<p>Item 8. Financial Statements</p>" + _cf_table()))
    assert not incorporated_by_reference(plain)
    assert gap_reason(plain, {"CF": object()}) == STATEMENT_NOT_LOCATED
    assert gap_reason(plain, {"IS": 1, "BS": 1, "CF": 1}) is None


# ---------- cell reading: zero-width cells, "$ (", caption dates, percent columns ----------

def test_zero_width_cell_does_not_hide_a_figure():
    body = _cf_table(extra=_tr("Diluted", "​", "18.50", "​", "25.62")).replace(
        '<tr><td></td><td>2025</td><td>2024</td></tr>',
        '<tr><td></td><td colspan="2">2025</td><td colspan="2">2024</td></tr>',
    )
    rows = {r.label: r for r in build_rows(_doc(body), "a")["CF"]}
    assert rows["Diluted"].cell_texts[0] == "18.50"


def test_currency_space_bracket_is_a_figure():
    rows = {r.label: r for r in build_rows(
        _doc(_cf_table(extra=_tr("Loss per share", "$ (0.03)", "$ (0.30)"))), "a")["CF"]}
    assert rows["Loss per share"].cell_texts == ("$ (0.03)", "$ (0.30)")  # a value row


def test_caption_cell_ending_in_date_is_a_period():
    body = _cf_table().replace(
        '<tr><td></td><td>2025</td><td>2024</td></tr>',
        '<tr><td></td><td>Years ended December 31, 2025</td><td>2024</td></tr>',
    )
    rows = build_rows(_doc(body), "a")["CF"]
    assert rows[0].column_periods == (date(2025, 12, 31), date(2024, 12, 31))


def test_caption_overlapping_a_year_column_is_not_a_period():
    body = _cf_table().replace(
        '<tr><td></td><td>2025</td><td>2024</td></tr>',
        '<tr><td></td><td colspan="2">Years ended December 31, 2025</td></tr>'
        '<tr><td></td><td>2025</td><td>2024</td></tr>',
    )
    assert len(build_rows(_doc(body), "a")["CF"][0].column_periods) == 2


PERCENT_IS = (
    "<p>(in millions)</p><table>"
    '<tr><td></td><td colspan="3">January 30, 2026</td><td colspan="3">January 31, 2025</td></tr>'
    '<tr><td>Current Earnings</td><td colspan="2">Amount</td><td>% Sales</td>'
    '<td colspan="2">Amount</td><td>% Sales</td></tr>'
    + _tr("Net sales", "$", "86,286", "100.00 %", "$", "83,674", "100.00 %")
    + _tr("Operating income", "", "10,000", "11.59", "", "9,000", "10.76")
    + _tr("Income tax provision", "", "2,000", "2.32", "", "1,900", "2.27")
    + _tr("Net earnings", "", "7,000", "8.11", "", "6,900", "8.25")
    + _tr("Diluted earnings per common share", "$", "12.10", "", "$", "11.90", "")
    + "</table>"
)


def test_percent_sub_column_is_a_non_period_column():
    rows = {r.label: r for r in build_rows(_doc(PERCENT_IS), "a")["IS"]}
    sales = rows["Net sales"]
    assert sales.column_kinds == ("amount", "percent", "amount", "percent")
    assert sales.column_periods == (date(2026, 1, 30), None, date(2025, 1, 31), None)
    assert sales.cell_texts == ("$ 86,286", "100.00 %", "$ 83,674", "100.00 %")
    assert not sales.is_amount_column(1) and sales.is_amount_column(0)
    assert "Current Earnings" not in rows  # the sub-heading row is header, not a row
    # Percent cells count as mapped for coverage.
    assert (sales.table_index, sales.source_row, 3) in covered_cells(rows.values())


def test_percent_detected_from_cells_when_heading_is_not_a_percentage():
    body = PERCENT_IS.replace("% Sales", "Of sales").replace(
        _tr("Operating income", "", "10,000", "11.59", "", "9,000", "10.76"),
        _tr("Operating income", "", "10,000", "11.59 %", "", "9,000", "10.76 %"),
    )
    rows = build_rows(_doc(body.replace(">2.32<", ">2.32 %<").replace(">2.27<", ">2.27 %<")
                           .replace(">8.11<", ">8.11 %<").replace(">8.25<", ">8.25 %<")), "a")
    assert rows["IS"][0].column_kinds == ("amount", "percent", "amount", "percent")


def test_amount_sub_columns_alone_are_not_split():
    body = PERCENT_IS.replace("% Sales", "Change")
    rows = build_rows(_doc(body.replace(" %<", "<")), "a")["IS"]
    assert rows[0].column_kinds == ("amount", "amount")


# ---------- period captions and figure-less rows (8.3 step 2) ----------

def test_is_period_caption():
    for caption in ("Fiscal Year Ended January 31,", "Years ended December 31, 2025",
                    "As of June 30,", "December 31,", "52 weeks ended February 1, 2025"):
        assert is_period_caption(caption), caption
    for label in ("Balance at December 31, 2024", "Total revenues", "Cash at end of year", ""):
        assert not is_period_caption(label), label


def test_caption_row_with_stray_figure_stays_in_the_header():
    # A caption row carrying a stray figure (e.g. invisible text in the label column) must not
    # end the header: the year row below it still gives the period columns.
    body = BALANCE_SHEET.replace(
        '<tr><td></td><td colspan="4">December 31,</td></tr>',
        '<tr><td>4</td><td colspan="4">December 31,</td></tr>',
    )
    rows = _rows(body)
    assert "December 31," not in [r.label for r in rows]
    assert rows[0].label == "Cash"
    assert rows[0].column_periods == (date(2024, 12, 31), date(2025, 12, 31))


def test_caption_and_empty_rows_in_the_body_are_not_rows():
    extra = (_tr("Fiscal Year Ended December 31,", "", "", "", "")
             + _tr("", "", "", "", ""))
    body = BALANCE_SHEET.replace(_tr("Goodwill", "", "3", "", "—", label_style="padding-left:1pt"),
                                 extra + _tr("Goodwill", "", "3", "", "—"))
    labels = [r.label for r in _rows(body)]
    assert "Fiscal Year Ended December 31," not in labels and "Goodwill" in labels


def test_row_without_figures_is_a_parent_header_and_empty_label_total_is_kept():
    body = BALANCE_SHEET.replace(
        _tr("Total current liabilities", "", "1", "", "1"),
        _tr("Total current liabilities", "", "1", "", "1") + _tr("", "", "1", "", "1"),
    )
    rows = _rows(body)
    assert "Current assets:" not in [r.label for r in rows]  # header, not a RowInstance
    assert rows[0].parent_header == "Current assets:"
    assert any(r.label == "" and r.cell_texts == ("1", "1") for r in rows)  # a figure row stays


# ---------- period ends on real filings (issue #41; 8.3 step 3) ----------

AS_OF = date(2026, 11, 24)
_DIGIT = re.compile(r"\d")


@lru_cache(maxsize=None)
def _records_by_document() -> dict[str, FilingRecord]:
    """Pinned train and validation 10-K documents -> their FilingRecord (held-out never read)."""
    store, records = EvidenceStore(EVIDENCE_DIR), {}
    for ticker, split in load_universe().items():
        if split == HELD_OUT:
            continue
        try:
            filings = store.load_filings(ticker, AS_OF)
        except KeyError:
            continue
        records.update({f.primary_document: f for f in filings})
    return records


def period_end_problems(rows: list[RowInstance], end: date) -> list[str]:
    """Figure rows missing the filing's own period end, or the prior-year column when shown.

    The prior year is any amount column ending within a week of one year before end (52/53-week
    years move by a few days).
    """
    problems = []
    for row in rows:
        amounts = [(p, t) for p, k, t in zip(row.column_periods, row.column_kinds, row.cell_texts)
                   if k == "amount"]
        figured = [p for p, t in amounts if _DIGIT.search(t)]
        if not figured:
            continue
        periods = {p for p, _ in amounts}
        if end not in periods:
            problems.append(f"{row.statement} '{row.label}': {end} not in {sorted(map(str, periods))}")
        elif len(figured) > 1 and not any(
            p is not None and abs((p - end.replace(year=end.year - 1)).days) <= 7 for p in periods
        ):
            problems.append(f"{row.statement} '{row.label}': no prior-year column in {periods}")
    return problems


def test_period_end_problems_flags_missing_and_prior_year():
    end = date(2025, 12, 31)
    row = RowInstance(
        row_id="a:BS:0", accession="a", statement="BS", label="Cash", parent_header=None,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=("5", "4"),
        column_periods=(end, date(2024, 12, 31)), unit_text=None, table_index=0, source_row=0,
        column_spans=((1, 1), (2, 2)), column_kinds=("amount", "amount"),
    )
    assert period_end_problems([row], end) == []
    week53 = dataclasses.replace(row, column_periods=(date(2025, 12, 28), date(2024, 12, 29)))
    assert period_end_problems([week53], date(2025, 12, 28)) == []  # prior year within a week
    unknown = dataclasses.replace(row, column_periods=(None, None))
    assert len(period_end_problems([unknown], end)) == 1
    no_prior = dataclasses.replace(row, column_periods=(end, date(2023, 12, 31)))
    assert "no prior-year column" in period_end_problems([no_prior], end)[0]
    header_only = dataclasses.replace(row, cell_texts=("", ""), column_periods=(None, None))
    assert period_end_problems([header_only], end) == []


def test_every_figure_row_has_the_filing_period_end(pinned_filing):
    record = _records_by_document().get(pinned_filing)
    if record is None:
        pytest.skip(f"{pinned_filing} is not in a five-year chain as of {AS_OF}")
    store = EvidenceStore(EVIDENCE_DIR)
    document = read_snapshot(store.evidence_dir, record.primary_document, store.manifest)
    fiscal_periods = store.fiscal_periods(record)
    filing = read_filing_rows(document, record.accession, fiscal_periods)
    rows = [r for statement_rows in filing.rows.values() for r in statement_rows]
    problems = period_end_problems(rows, record.end)
    assert not problems, f"{len(problems)} rows, e.g. {problems[:3]}"
    disagreements = period_disagreements(rows, fiscal_periods)
    assert not disagreements, f"{len(disagreements)} columns, e.g. {disagreements[:3]}"


def test_title_dates_one_end_per_bare_year():
    title = ("STATEMENTS OF CONSOLIDATED INCOME For the Years Ended November 2, 2025, "
             "October 27, 2024, and October 29, 2023")
    assert title_dates(title, [2025, 2024, 2023]) == [
        date(2025, 11, 2), date(2024, 10, 27), date(2023, 10, 29)]
    assert title_dates("BALANCE SHEETS At January 2, 2022 and January 3, 2021", [2021, 2020]) == [
        date(2022, 1, 2), date(2021, 1, 3)]  # a January year end belongs to the prior label
    assert title_dates("Fiscal years ended Dec 27,2025 and Dec 28,2024", [2025, 2024]) == [
        date(2025, 12, 27), date(2024, 12, 28)]


def test_title_dates_refuses_unrelated_dates():
    audit = "/s/ KPMG LLP Atlanta, Georgia March 18, 2026 CONSOLIDATED STATEMENTS OF EARNINGS"
    assert title_dates(audit, [2025, 2024, 2023]) is None  # too few dates
    assert title_dates(audit, [2025]) is None  # March of the next year does not fit 2025
    assert title_dates("December 31, 2024 and December 31, 2025", [2025, 2024]) is None  # order
    assert title_dates("anything", []) is None


def test_title_month_day_only_without_a_year():
    assert title_month_day("BALANCE SHEETS December 31,") == ("December", "31")
    assert title_month_day("(in millions) As of June 30") == ("June", "30")
    # A date with a year names one year only, and a report date none: never carried over.
    assert title_month_day("BALANCE SHEETS As of December 31, 2025") is None
    assert title_month_day("Dallas, Texas February 9, 2026 ITEM 8. STATEMENTS") is None


def test_stated_dates_expand_year_lists():
    assert stated_dates("December 31, 2025 and 2024 (in millions)") == [
        date(2025, 12, 31), date(2024, 12, 31)]
    assert stated_dates("ended December 27, 2025, December 28, 2024 and Dec 30,2023") == [
        date(2025, 12, 27), date(2024, 12, 28), date(2023, 12, 30)]
    assert title_dates("BALANCE SHEETS December 31, 2025 and 2024", [2025, 2024]) == [
        date(2025, 12, 31), date(2024, 12, 31)]
    assert stated_dates("no dates") == []


def test_bare_years_with_audit_date_above_are_unknown_not_dated():
    body = BALANCE_SHEET.replace(
        "<p>CONSOLIDATED BALANCE SHEETS (in millions)</p>",
        "<p>/s/ Auditor LLP Dallas, Texas February 9, 2026</p>"
        "<p>CONSOLIDATED BALANCE SHEETS (in millions)</p>",
    ).replace('<tr><td></td><td colspan="4">December 31,</td></tr>', "")
    assert _rows(body)[0].column_periods == (None, None)


def test_header_dates_with_abbreviated_month_and_no_space():
    body = BALANCE_SHEET.replace(
        '<tr><td></td><td colspan="4">December 31,</td></tr>'
        '<tr><td></td><td colspan="2">2024</td><td colspan="2">2025</td></tr>',
        '<tr><td></td><td colspan="2">Dec 29,2024</td><td colspan="2">Sept. 28, 2025</td></tr>',
    )
    assert _rows(body)[0].column_periods == (date(2024, 12, 29), date(2025, 9, 28))


BARE_YEARS_BS = BALANCE_SHEET.replace('<tr><td></td><td colspan="4">December 31,</td></tr>', "")
FISCAL = {2025: date(2026, 1, 31), 2024: date(2025, 2, 1), 2023: date(2024, 2, 3)}


def test_bare_years_take_the_fiscal_periods_when_the_document_gives_no_date():
    assert _rows(BARE_YEARS_BS)[0].column_periods == (None, None)
    rows = build_rows(_doc(BARE_YEARS_BS), "a", fiscal_periods=FISCAL)["BS"]
    assert rows[0].column_periods == (date(2025, 2, 1), date(2026, 1, 31))  # labels 2024, 2025


def test_document_dates_win_over_fiscal_periods():
    rows = build_rows(_doc(BALANCE_SHEET), "a", fiscal_periods=FISCAL)["BS"]
    assert rows[0].column_periods == (date(2024, 12, 31), date(2025, 12, 31))  # "December 31,"


def test_bare_year_without_a_fiscal_period_stays_unknown():
    rows = build_rows(_doc(BARE_YEARS_BS), "a", fiscal_periods={2025: date(2026, 1, 31)})["BS"]
    assert rows[0].column_periods == (None, date(2026, 1, 31))  # 2024 is not listed: a gap
