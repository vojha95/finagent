import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from finagent.document import (
    build_rows,
    cell_coverage,
    covered_cells,
    indent_points,
    item_8_table_start,
    label_cell_addresses,
    locate_statements,
    parse_style,
    preceding_texts,
    to_points,
)
from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import read_snapshot
from finagent.ixbrl import build_tag_store, parse_document
from finagent.ixbrl.clean import clean_document
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
    document = read_snapshot(EVIDENCE_DIR, document_name, MANIFEST)
    clean = clean_document(document)
    return document, clean, build_rows(clean, accession)


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
