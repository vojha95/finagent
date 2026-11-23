from datetime import date
from decimal import Decimal

import pytest

from finagent.ixbrl import (
    CellAddress,
    TableAlignmentError,
    TagFact,
    build_tag_store,
    clean_document,
    parse_document,
    tagged_table_counts,
    visible_tables,
)

CONTEXT = (
    '<xbrli:context id="d"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier>'
    "</xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>"
    '</xbrli:context><xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
)


def _fact_cell(name: str, fact_id: str, value: str = "1") -> str:
    return (f'<td><ix:nonFraction name="x:{name}" contextRef="d" unitRef="usd" scale="6" '
            f'id="{fact_id}">{value}</ix:nonFraction></td>')


# A table inside ix:header (ix:hidden) is deleted by the clean document; the visible tables
# after it must keep the clean document's numbering (issues log #34).
DOC = f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"><body>
<ix:header><ix:hidden><table><tr><td>Hidden</td>{_fact_cell("Hidden", "h1")}</tr></table>
</ix:hidden><ix:resources>{CONTEXT}</ix:resources></ix:header>
<table><tr><td>Cash</td>{_fact_cell("Cash", "c1")}{_fact_cell("CashPrior", "c2")}</tr>
<tr><td>Nil</td><td><ix:nonFraction name="x:Nil" contextRef="d" unitRef="usd"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true" id="n1"/></td></tr></table>
<table><tr><td>Debt</td>{_fact_cell("Debt", "d1")}</tr></table>
</body></html>""".encode()


def test_tag_store_numbers_only_visible_tables():
    facts = {f.fact_id: f for f in build_tag_store(DOC, "a")}
    assert facts["h1"].cell_address is None  # inside ix:header: not a visible cell
    assert facts["c1"].cell_address.table_index == 0
    assert facts["d1"].cell_address.table_index == 1
    assert len(visible_tables(parse_document(DOC))) == 2


def test_tagged_table_counts_keyed_by_clean_document_tables():
    clean = clean_document(DOC)
    counts = tagged_table_counts(DOC, clean, build_tag_store(DOC, "a"))
    assert counts == {0: 2, 1: 1}  # nil fact not counted; hidden table not counted


def test_nested_facts_in_one_cell_count_once():
    fact = TagFact(
        accession="a", concept="x:A", context_id="d", start=None, end=date(2025, 12, 31),
        dimensions={}, unit="USD", scale=6, sign=1, amount=Decimal(1),
        cell_address=CellAddress(0, 0, 1, "Cash"),
    )
    assert tagged_table_counts(DOC, clean_document(DOC), [fact, fact]) == {0: 1}


def test_table_count_mismatch_raises():
    clean = clean_document(DOC)
    cut = clean.index(b"<table><tr><td>Debt")
    other_clean = clean[:cut] + clean[clean.index(b"</table>", cut) + len(b"</table>"):]
    with pytest.raises(TableAlignmentError, match="visible tables"):
        tagged_table_counts(DOC, other_clean, build_tag_store(DOC, "a"))


def test_fact_outside_clean_tables_raises():
    stray = TagFact(
        accession="a", concept="x:A", context_id="d", start=None, end=date(2025, 12, 31),
        dimensions={}, unit="USD", scale=6, sign=1, amount=Decimal(1),
        cell_address=CellAddress(7, 0, 1, "Cash"), fact_id="s1",
    )
    with pytest.raises(TableAlignmentError, match="table 7"):
        tagged_table_counts(DOC, clean_document(DOC), [stray])
