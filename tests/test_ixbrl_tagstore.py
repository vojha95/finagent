import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import load_manifest, read_snapshot
from finagent.ixbrl import (
    CellAddress,
    TagParseError,
    build_tag_store,
    parse_amount,
    parse_document,
    row_label,
    table_grid,
)

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"

DOC = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<body>
<ix:header><ix:hidden>
<ix:nonFraction name="x:Hidden" contextRef="d" unitRef="usd" scale="0" id="h1">5</ix:nonFraction>
</ix:hidden><ix:resources>
<xbrli:context id="d"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="i"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="a:Axis">a:Member</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
<xbrli:unit id="sh"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
</ix:resources></ix:header>
<p>Intro <ix:nonFraction name="x:InText" contextRef="d" unitRef="sh" scale="0" id="t0">7</ix:nonFraction></p>
<table>
<tr><td colspan="2">Header</td><td>2025</td></tr>
<tr><td><span>Capex</span></td><td>$</td>
<td>(<ix:nonFraction name="x:Capex" contextRef="d" unitRef="usd" scale="6" decimals="-6"
 format="ixt:num-dot-decimal" id="c1">131,819</ix:nonFraction>)</td></tr>
<tr><td>Loss</td><td>$</td>
<td><ix:nonFraction name="x:Loss" contextRef="i" unitRef="usd" scale="9" sign="-"
 format="ixt:num-dot-decimal" id="c2">1.3</ix:nonFraction></td></tr>
<tr><td>Debt issued</td><td>$</td>
<td><ix:nonFraction name="x:Debt" contextRef="d" unitRef="usd" scale="6" format="ixt:fixed-zero" id="c3">&#8212;</ix:nonFraction></td></tr>
<tr><td>Contingencies</td><td/><td><ix:nonFraction name="x:Nil" contextRef="d" unitRef="usd" xsi:nil="true" id="c4"/></td></tr>
<tr><td>Par</td><td/><td><ix:nonFraction name="x:Outer" contextRef="d" unitRef="sh" scale="6" id="o1"><ix:nonFraction name="x:Inner" contextRef="i" unitRef="sh" scale="6" id="o2">500</ix:nonFraction></ix:nonFraction></td></tr>
</table>
</body></html>"""


@pytest.fixture(scope="module")
def synthetic() -> dict[str, object]:
    return {f.fact_id: f for f in build_tag_store(DOC.encode(), "0000000000-25-000001")}


# ---------- parse_amount ----------

@pytest.mark.parametrize(
    "text, fmt, scale, sign, expected",
    [
        ("131,819", "ixt:num-dot-decimal", 6, 1, Decimal("131819000000")),
        ("1.3", "ixt:num-dot-decimal", 9, -1, Decimal("-1300000000")),
        ("2,000", "ixt:numdotdecimal", 0, 1, Decimal("2000")),
        ("0.01", None, 0, 1, Decimal("0.01")),
        ("—", "ixt:fixed-zero", 6, 1, Decimal("0")),
        ("Two", "ixt-sec:numwordsen", 0, 1, Decimal("2")),
        ("twenty-one", "ixt-sec:numwordsen", 0, 1, Decimal("21")),
        ("no", "ixt-sec:numwordsen", 0, 1, Decimal("0")),
        # xs:decimal (no format): integer part may be omitted, leading "+" allowed
        (".96", None, 0, 1, Decimal("0.96")),
        (".05", None, 0, 1, Decimal("0.05")),
        (".530", None, 0, 1, Decimal("0.530")),
        ("+70", None, 0, 1, Decimal("70")),
        ("+1.5", None, 6, -1, Decimal("-1500000")),
        # ixt:num-dot-decimal: integer part may be omitted
        (".9", "ixt:num-dot-decimal", 0, 1, Decimal("0.9")),
        # ixt-sec:numwordsen: scale words and zero words
        ("one million", "ixt-sec:numwordsen", 0, 1, Decimal("1000000")),
        ("two million", "ixt-sec:numwordsen", 0, 1, Decimal("2000000")),
        ("Two Million", "ixt-sec:numwordsen", 3, 1, Decimal("2000000000")),
        ("nil", "ixt-sec:numwordsen", 9, 1, Decimal("0")),
        ("none", "ixt-sec:numwordsen", 0, 1, Decimal("0")),
        ("one hundred", "ixt-sec:numwordsen", 0, 1, Decimal("100")),
        (
            "one hundred twenty-three thousand four hundred and five",
            "ixt-sec:numwordsen", 0, 1, Decimal("123405"),
        ),
        ("one billion two hundred million", "ixt-sec:numwordsen", 0, 1, Decimal("1200000000")),
    ],
)
def test_parse_amount(text, fmt, scale, sign, expected):
    assert parse_amount(text, fmt, scale, sign) == expected


@pytest.mark.parametrize(
    "text, fmt",
    [
        ("12", "ixt:num-comma-decimal"), ("(5)", None), ("", "ixt:num-dot-decimal"),
        ("several", "ixt-sec:numwordsen"),
        ("-5", None), ("+", None), (".", None), ("1.2.3", None), ("5.", None),
        ("+70", "ixt:num-dot-decimal"), (".", "ixt:num-dot-decimal"),
        ("million", "ixt-sec:numwordsen"), ("thousand million", "ixt-sec:numwordsen"),
        ("two million one million", "ixt-sec:numwordsen"),
        ("zero million", "ixt-sec:numwordsen"), ("nil million", "ixt-sec:numwordsen"),
        ("one hundred zero", "ixt-sec:numwordsen"), ("twenty hundred", "ixt-sec:numwordsen"),
        ("", "ixt-sec:numwordsen"),
    ],
)
def test_parse_amount_refuses_to_guess(text, fmt):
    with pytest.raises(TagParseError):
        parse_amount(text, fmt, 0, 1)


# ---------- table helpers ----------

def test_table_grid_expands_colspan_and_rowspan():
    root = parse_document(
        b"<table><tr><td rowspan='2'>A</td><td colspan='2'>B</td><td>C</td></tr>"
        b"<tr><td>D</td><td>E</td><td>F</td></tr></table>"
    )
    grid = table_grid(root)
    by_text = {cell.text: pos for cell, pos in grid.items()}
    assert by_text == {"A": (0, 0), "B": (0, 1), "C": (0, 3), "D": (1, 1), "E": (1, 2), "F": (1, 3)}


def test_row_label_skips_numeric_cells():
    root = parse_document(
        b"<table><tr><td>$</td><td>(1,234)</td><td> Net sales </td><td>Other</td></tr>"
        b"<tr><td>2025</td><td>\xe2\x80\x94</td></tr></table>"
    )
    first, second = root.findall("tr")
    assert row_label(first) == "Net sales"
    assert row_label(second) is None


# ---------- build_tag_store on a synthetic filing ----------

def test_every_nonfraction_becomes_a_fact(synthetic):
    assert set(synthetic) == {"h1", "t0", "c1", "c2", "c3", "c4", "o1", "o2"}


def test_fact_fields(synthetic):
    capex = synthetic["c1"]
    assert capex.accession == "0000000000-25-000001"
    assert capex.concept == "x:Capex"
    assert (capex.start, capex.end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert capex.dimensions == {}
    assert (capex.unit, capex.scale, capex.sign, capex.decimals) == ("USD", 6, 1, "-6")
    assert capex.amount == Decimal("131819000000")  # parentheses are display only
    assert capex.cell_address == CellAddress(0, 1, 2, "Capex")


def test_sign_dimensions_and_instant(synthetic):
    loss = synthetic["c2"]
    assert loss.sign == -1 and loss.amount == Decimal("-1300000000")
    assert loss.start is None and loss.end == date(2025, 12, 31)
    assert loss.dimensions == {"a:Axis": "a:Member"}


def test_zero_dash_and_nil(synthetic):
    assert synthetic["c3"].amount == Decimal("0")
    assert synthetic["c3"].format == "ixt:fixed-zero"
    nil = synthetic["c4"]
    assert nil.is_nil and nil.amount is None  # missing is never zero


def test_nested_facts_share_value_and_cell(synthetic):
    outer, inner = synthetic["o1"], synthetic["o2"]
    assert outer.amount == inner.amount == Decimal("500000000")
    assert outer.cell_address == inner.cell_address
    assert outer.cell_address.row_label == "Par"


def test_facts_outside_tables_have_no_cell_address(synthetic):
    assert synthetic["h1"].cell_address is None
    assert synthetic["t0"].cell_address is None
    assert synthetic["t0"].unit == "shares"


def test_unknown_context_raises():
    bad = DOC.replace('contextRef="d" unitRef="usd" scale="6" decimals', 'contextRef="zz" unitRef="usd" scale="6" decimals')
    with pytest.raises(TagParseError):
        build_tag_store(bad.encode(), "a")


# ---------- pinned SEC filings ----------

def _filing_bytes(name: str) -> bytes:
    return read_snapshot(EVIDENCE_DIR, name, load_manifest(EVIDENCE_DIR))


@pytest.fixture(scope="module")
def amazon_fy2025():
    filings = EvidenceStore(EVIDENCE_DIR).load_filings("AMZN", date(2026, 12, 31))
    record = next(f for f in filings if f.fiscal_year == 2025)
    facts = build_tag_store(_filing_bytes(record.primary_document), record.accession)
    return record, facts


def _primary(facts, record, concept):
    """The non-dimensional, full-fiscal-year fact for a concept, located in a table."""
    found = [
        f for f in facts
        if f.concept == concept and not f.dimensions
        and f.start == record.start and f.end == record.end and f.cell_address is not None
    ]
    assert found, concept
    assert len({f.amount for f in found}) == 1, concept
    return found[0]


def test_amazon_fy2025_total_net_sales(amazon_fy2025):
    record, facts = amazon_fy2025
    fact = _primary(facts, record, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")
    assert fact.amount == Decimal("716924000000")
    assert fact.unit == "USD" and fact.sign == 1
    assert fact.accession == record.accession
    assert fact.cell_address.row_label == "Total net sales"


def test_amazon_fy2025_purchases_of_property_and_equipment(amazon_fy2025):
    record, facts = amazon_fy2025
    fact = _primary(facts, record, "us-gaap:PaymentsToAcquireProductiveAssets")
    # Shown as (131,819) in the cash flow statement; the tag is a positive cash payment.
    assert fact.amount == Decimal("131819000000")
    assert fact.sign == 1 and fact.amount > 0
    assert fact.cell_address.row_label == "Purchases of property and equipment"


def test_registry_formats_parse_end_to_end():
    """Regression: 'nil' (numwordsen) in COP FY2025 is a reported zero, not xsi:nil.

    The leading-dot num-dot-decimal case ('.9') has no train or validation filing; it is
    covered by test_parse_amount.
    """
    facts = build_tag_store(_filing_bytes("cop-20251231.htm"), "accession")
    assert facts and all(f.amount is not None or f.is_nil for f in facts)
    nil_words = [f for f in facts if f.format and f.format.endswith("numwordsen")
                 and f.amount == 0]
    assert nil_words and all(not f.is_nil for f in nil_words)


def test_every_pinned_filing_parses_completely(pinned_filing):
    document = _filing_bytes(pinned_filing)
    facts = build_tag_store(document, "accession")
    assert len(facts) == len(re.findall(rb"<ix:nonFraction\b", document))
    assert all(f.amount is not None or f.is_nil for f in facts)
