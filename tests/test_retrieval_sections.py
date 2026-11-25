"""Blocks and sections of a clean document (finagent.retrieval.sections; 8.14)."""

from pathlib import Path

import pytest
from retrieval_support import DOC, NO_HEADINGS, POINTERS, TEXT_TOC

from finagent.config import ConfigError, load_retrieval_config
from finagent.ixbrl.tagstore import parse_document
from finagent.retrieval.sections import (UNKNOWN, Block, detect_sections, document_blocks,
                                         item_heading, mask_abbreviations, note_headings,
                                         note_number, note_title, ordered_run, squeeze,
                                         toc_runs)

CONFIG = load_retrieval_config()


def blocks(doc: str) -> list[Block]:
    return document_blocks(parse_document(doc.encode()), CONFIG)


def test_blocks_text_tables_layout_and_furniture():
    b = blocks(DOC)
    texts = [x.text for x in b if x.kind == "text"]
    assert "Form" not in " ".join(texts) and "p {}" not in " ".join(texts)  # head skipped
    assert "12" not in texts  # page number dropped
    tables = [x for x in b if x.kind == "table"]
    assert len(tables) == 2  # the contents table and the revenue table
    assert "Revenue | $ | 1,234\nCost | (567)" in [t.text for t in tables]
    assert "• Liquidity remains sufficient for our cash requirements." in texts  # layout table
    assert ["Lead text with inline words", "A nested block.", "tail text"] == \
        texts[texts.index("Lead text with inline words"):texts.index("tail text") + 1]


def test_item_heading_rules():
    assert item_heading(Block("text", "ITEM 7. MANAGEMENT'S DISCUSSION"), CONFIG) == "7"
    assert item_heading(Block("text", "Item 7A."), CONFIG) == "7A"
    assert item_heading(Block("text", "Item 7"), CONFIG) is None  # running page header
    assert item_heading(Block("text", "Item 1A of Part I describes risks"), CONFIG) is None
    assert item_heading(Block("text", "Item 7. " + "x" * 300), CONFIG) is None  # too long
    assert item_heading(Block("table", "Item 7. | MD&A | 20"), CONFIG) is None


def test_sections_of_the_synthetic_filing():
    b = blocks(DOC)
    m = detect_sections(b, CONFIG)
    ids = [s.section_id for s in m.sections]
    assert ids == [UNKNOWN, "ITEM_1", "ITEM_1A", "ITEM_7", "ITEM_8", "ITEM_8/NOTE_01",
                   "ITEM_8/NOTE_02", "ITEM_9"]
    assert m.missing == ("7A",) and m.unknown_reason.startswith("text before the first Item")
    unknown = m.sections[0]
    assert unknown.heading == "" and unknown.words == 9  # the cover line; the contents table
    assert m.section_at(b.index(next(x for x in b if x.text.startswith("Borrowings")))) == \
        "ITEM_8/NOTE_02"
    spans = [(s.start_block, s.end_block) for s in m.sections]
    assert spans[0][0] == 0 and spans[-1][1] == len(b)
    assert all(a[1] == c[0] for a, c in zip(spans, spans[1:]))  # contiguous, no gaps


def test_notes_must_follow_in_sequence():
    b = [Block("text", t) for t in ("Note 1 — A", "x", "Note 3 — C", "Note 2 — B",
                                    "1. Policies", "Note 3 — C")]
    assert note_headings(b, 0, len(b), CONFIG) == [(0, 1), (3, 2), (5, 3)]
    numbered = [Block("text", t) for t in ("1. Summary of Policies", "2. Debt",
                                           "3. The company paid cash.")]
    assert note_headings(numbered, 0, 3, CONFIG) == [(0, 1), (1, 2)]


def test_text_contents_list_is_left_out():
    b = blocks(TEXT_TOC)
    candidates = [(i, item_heading(x, CONFIG)) for i, x in enumerate(b)
                  if item_heading(x, CONFIG)]
    toc = toc_runs(b, candidates, CONFIG)
    assert len(toc) == 4  # every entry of the list (page numbers are dropped as furniture)
    m = detect_sections(b, CONFIG)
    body = [s for s in m.sections if s.section_id != UNKNOWN]
    assert [s.section_id for s in body] == ["ITEM_1", "ITEM_7"]
    assert m.toc_headings == 4 and m.ignored_headings == ()


def test_ordered_run_prefers_the_longest_then_earliest():
    items = CONFIG.items
    assert ordered_run([(0, "7"), (1, "1"), (2, "1A"), (3, "7"), (4, "8")], items) == \
        [(1, "1"), (2, "1A"), (3, "7"), (4, "8")]
    assert ordered_run([(0, "1"), (1, "7"), (2, "7"), (3, "8")], items) == \
        [(0, "1"), (1, "7"), (3, "8")]
    assert ordered_run([], items) == []


def test_document_without_headings_is_unknown_never_guessed():
    m = detect_sections(blocks(NO_HEADINGS), CONFIG)
    assert [s.section_id for s in m.sections] == [UNKNOWN]
    assert m.unknown_reason == "no Item heading outside the table of contents"
    assert m.missing == CONFIG.expected_items


def test_retrieval_config_validation(tmp_path):
    path = tmp_path / "r.yaml"
    source = (Path(__file__).parents[1] / "configs" / "retrieval.yaml").read_text("utf-8")
    path.write_text(source.replace("target_words: 150", "target_words: 300"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_retrieval_config(path)
    path.write_text(source.replace('expected: ["1"', 'expected: ["99"'), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_retrieval_config(path)


def test_pointer_items_take_their_content_from_a_later_financial_section():  # #171
    b = blocks(POINTERS)
    m = detect_sections(b, CONFIG)
    ids = [s.section_id for s in m.sections]
    assert ids == ["ITEM_1", "ITEM_7/POINTER", "ITEM_8/POINTER", "ITEM_9A", "ITEM_14", "ITEM_7",
                   "ITEM_8", "ITEM_8/NOTE_01", "ITEM_8/NOTE_02", "ITEM_15"]
    by_id = {s.section_id: s for s in m.sections}
    assert by_id["ITEM_7"].heading == "MANAGEMENT’S DISCUSSION AND ANALYSIS"
    assert by_id["ITEM_8"].heading == "REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM"
    # the auditor's report in Item 9A has no statements after it: not the Item 8 content
    assert m.section_at(next(i for i, x in enumerate(b) if x.text.startswith("We audited"))) \
        == "ITEM_9A"
    assert by_id["ITEM_8/NOTE_01"].heading == "NOTE 1 SUMMARY OF ACCOUNTING POLICIES"
    assert by_id["ITEM_8/NOTE_02"].heading == "2. Spin-Off of Widget & Co."
    at7, at8 = by_id["ITEM_7"].start_block, by_id["ITEM_8"].start_block
    assert m.relocated == (f"7 from block {at7} after ITEM_14 (MANAGEMENT’S DISCUSSION AND "
                           "ANALYSIS)", f"8 from block {at8} after ITEM_14 (REPORT OF INDEPENDENT "
                           "REGISTERED PUBLIC ACCOUNTING FIRM)")
    assert m.pointers == () and m.missing == ("1A", "7A")
    assert all(x.text for x in b)  # zero-width fillers are dropped


def test_pointer_without_content_stays_and_is_reported():
    doc = POINTERS.split("<div>&#8203;</div>")[0] + "</body></html>"
    m = detect_sections(blocks(doc), CONFIG)
    assert [s.section_id for s in m.sections][:3] == ["ITEM_1", "ITEM_7", "ITEM_8"]
    assert m.pointers == ("7", "8") and m.relocated == ()


def test_note_title_rules():
    assert mask_abbreviations("3. Spin-Off of Organon & Co.", CONFIG.abbreviations) == \
        "3. Spin-Off of Organon & Co"
    assert mask_abbreviations("U.S. Pension Plans", CONFIG.abbreviations) == "US Pension Plans"
    b = [Block("text", t) for t in ("NOTE 1", "SUMMARY OF POLICIES", "NOTE 2", "This is prose.",
                                    "2.", "DEBT", "3. The company paid cash.")]
    assert note_number(b, 0, CONFIG) == 1 and note_number(b, 2, CONFIG) is None
    assert note_number(b, 4, CONFIG) == 2 and note_number(b, 6, CONFIG) is None
    assert note_title(Block("text", "Debt and Credit Facilities"), CONFIG)
    assert squeeze("CONSOLIDATED B ALANCE SHEETS") == "consolidatedbalancesheets"
