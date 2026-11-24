"""Face-statement cells in both channels and the face-block sum (total liabilities rule)."""

import re
from decimal import Decimal

from statements_support import END, PRIOR_END, fact, millions, row

from finagent.statements.face_block import (
    DISAGREE,
    DOCUMENT_ONLY,
    EMPTY_BLOCK,
    NO_ANCHOR_ROW,
    NO_STOP_ROW,
    STATEMENT_NOT_LOCATED,
    VERIFIED,
    cell_facts,
    column_for,
    face_block,
    facts_by_cell,
    is_subtotal,
    matches_stop,
    read_cell,
    rows_by_cell,
)

STOPS = tuple(re.compile(p, re.IGNORECASE) for p in (
    "^commitments", "^(total )?(stockholders|shareholders)['’]? equity", "^common stock"))


def _bs(*specs):
    """rows from (label, current, prior, is_total) tuples, positions in order."""
    return [row(i, label, texts=(cur, pri), is_total=total)
            for i, (label, cur, pri, total) in enumerate(specs)]


def test_column_for_and_cell_facts():
    r = row(0, "Accounts payable")
    assert column_for(r, 4) == 0 and column_for(r, 9) == 1 and column_for(r, 7) is None
    facts = [fact("us-gaap:AP", millions(100), cell=(1, 0, 3)),
             fact("us-gaap:AP", millions(90), end=PRIOR_END, cell=(1, 0, 9)),
             fact("us-gaap:AP", millions(1), cell=(1, 0, 3), dims={"a:Axis": "a:M"})]
    found = cell_facts(r, END, facts_by_cell(facts))
    assert [f.amount for f in found] == [millions(100)]
    assert rows_by_cell([r]) == {(1, 0): r}


def test_read_cell_statuses():
    r = row(0, "Long-term debt")
    agree = read_cell(r, END, facts_by_cell([fact("us-gaap:D", 100_400_000, cell=(1, 0, 3))]))
    assert agree.status == VERIFIED and agree.value == Decimal(100_400_000)  # the tag amount
    differ = read_cell(r, END, facts_by_cell([fact("us-gaap:D", millions(70), cell=(1, 0, 3))]))
    assert differ.status == DISAGREE and differ.value is None
    alone = read_cell(r, END, {})
    assert alone.status == DOCUMENT_ONLY and alone.value == millions(100)
    assert read_cell(row(1, "Blank", texts=("", "")), END, {}) is None


def test_face_block_sums_anchor_and_rows_down_to_the_stop_row():
    rows = _bs(("Accounts payable", "60", "50", False),
               ("Total current liabilities", "100", "90", True),
               ("Long-term lease liabilities", "30", "20", False),
               ("Long-term debt", "50", "40", False),
               ("Commitments and contingencies", "", "", False),
               ("Common stock", "5", "5", False))
    facts = [fact("us-gaap:LC", millions(100), cell=(1, 1, 3)),
             fact("us-gaap:LL", millions(30), cell=(1, 2, 3))]
    block = face_block(rows, rows[1].row_id, END, STOPS, facts, include_anchor=True)
    assert block.gap_reason is None and block.stop_row == "Commitments and contingencies"
    assert block.total == millions(180)
    assert [c.status for c in block.components] == [VERIFIED, VERIFIED, DOCUMENT_ONLY]
    assert block.status == DOCUMENT_ONLY  # weakest component
    without_anchor = face_block(rows, rows[1].row_id, END, STOPS, facts)
    assert without_anchor.total == millions(80)


def test_subtotal_that_foots_is_skipped_and_a_bold_line_item_is_kept():
    rows = _bs(("Total current liabilities", "100", "90", True),
               ("Long-Term Debt", "50", "40", True),  # bold single line, not a subtotal
               ("Deferred income taxes", "20", "10", False),
               ("Other noncurrent liabilities", "10", "10", False),
               ("Total deferred credits and other", "30", "20", True),  # foots: 20 + 10
               ("Total stockholders' equity", "500", "400", True))
    block = face_block(rows, rows[0].row_id, END, STOPS, [], include_anchor=True)
    assert block.total == millions(180)
    assert block.skipped == ("Total deferred credits and other",)
    assert block.stop_row == "Total stockholders' equity"


def test_is_subtotal_needs_a_total_style_and_footing():
    above = [read_cell(r, END, {}) for r in _bs(("a", "20", "1", False), ("b", "10", "1", False))]
    total = row(5, "Total other", texts=("30", "2"), is_total=True)
    assert is_subtotal(total, read_cell(total, END, {}), above)
    assert not is_subtotal(total, read_cell(total, END, {}), [])
    plain = row(5, "Other", texts=("30", "2"))
    assert not is_subtotal(plain, read_cell(plain, END, {}), above)
    wrong = row(5, "Total other", texts=("31", "2"), is_total=True)
    assert not is_subtotal(wrong, read_cell(wrong, END, {}), above)


def test_face_block_gaps():
    rows = _bs(("Total current liabilities", "100", "90", True), ("Long-term debt", "50", "40",
                                                                   False))
    assert face_block([], "x", END, STOPS, []).gap_reason == STATEMENT_NOT_LOCATED
    assert face_block(rows, "missing", END, STOPS, []).gap_reason == NO_ANCHOR_ROW
    assert face_block(rows, rows[0].row_id, END, STOPS, []).gap_reason == NO_STOP_ROW
    empty = _bs(("Total current liabilities", "100", "90", True), ("Common stock", "5", "5",
                                                                   False))
    assert face_block(empty, empty[0].row_id, END, STOPS, [], True).gap_reason == EMPTY_BLOCK
    no_value = _bs(("Total current liabilities", "", "", True), ("Common stock", "5", "5", False))
    assert face_block(no_value, no_value[0].row_id, END, STOPS, [], True).gap_reason == \
        NO_ANCHOR_ROW


def test_disagreeing_component_gives_no_total():
    rows = _bs(("Total current liabilities", "100", "90", True), ("Long-term debt", "50", "40",
                                                                   False),
               ("Common stock", "5", "5", False))
    facts = [fact("us-gaap:D", millions(55), cell=(1, 1, 3))]
    block = face_block(rows, rows[0].row_id, END, STOPS, facts, include_anchor=True)
    assert block.total is None and block.status == DISAGREE


def test_matches_stop_reads_label_and_parent_header():
    assert matches_stop(row(0, "Commitments and contingencies (Note 7)"), STOPS)
    assert matches_stop(row(0, "Preferred", parent="Stockholders' equity:"), STOPS)
    assert not matches_stop(row(0, "Long-term debt", parent="Liabilities and stockholders' equity"),
                            STOPS)
