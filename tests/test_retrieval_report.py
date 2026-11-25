"""The retrieval run's per-filing summary and report (finagent.retrieval.report)."""

from retrieval_support import DOC, filing

from finagent.config import load_retrieval_config
from finagent.retrieval.index import build_index
from finagent.retrieval.report import (FilingResult, comparison_rows, load_previous,
                                       render_report, section_words, summary_row,
                                       visible_words, write_outputs)

HEADER = {"run_id": "retrieval-test", "git_commit": "abc", "git_dirty": False,
          "evidence_manifest_sha256": "0" * 64, "as_of": "2026-11-24"}


def result() -> FilingResult:
    f = filing()
    r = FilingResult("SYNT", f, build_index(f, DOC.encode(), load_retrieval_config()),
                     visible_words(DOC.encode()))
    r.smoke.append(("R5", f"{f.accession}:ITEM_8/NOTE_02:0000", 3.2))
    return r


def test_visible_words_leave_out_the_head():
    assert visible_words(b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>a b</title>'
                         b'</head><body><p>one two <b>three</b></p></body></html>') == 3


def test_summary_row_and_report(tmp_path):
    r = result()
    row = summary_row(r)
    assert row[:6] == ["SYNT", "FY2025", r.filing.accession, 5, "7A", 2]
    assert row[6].startswith("9 (text before the first Item heading")
    assert row[-1].endswith("%") and float(row[-1][:-1]) > 90
    error = FilingResult("SYNT", r.filing, error="ValueError: bad")
    assert summary_row(error)[3:5] == ["ERROR", "ValueError: bad"]
    text = render_report([r, error], HEADER)
    assert "frozen 2026-11-25T00:23:25Z" in text and "| SYNT | FY2025 | R5 |" in text
    write_outputs(tmp_path, [r, error])
    assert (tmp_path / "sections.csv").read_text().count("\n") == 1 + len(r.index.sections.sections)
    assert (tmp_path / "chunks.csv").read_text().count("\n") == 1 + len(r.index.chunks)


def test_comparison_with_a_previous_run(tmp_path):  # #171, #172
    r = result()
    write_outputs(tmp_path, [r])
    previous = load_previous(tmp_path)
    assert previous[r.filing.accession]["notes"] == 2
    assert comparison_rows([r], previous) == []  # the same run: nothing changed
    previous[r.filing.accession]["notes"] = 0
    row = comparison_rows([r], previous)[0]
    assert row[:2] == ["SYNT", "FY2025"] and row[4] == "0 → 2" and row[5] == "none"
    words = section_words(r)
    assert words["ITEM_8"] > 0 and "ITEM_8/NOTE_01" not in words
    text = render_report([r], dict(HEADER, compare_with="retrieval-old"), previous)
    assert "Changes against `retrieval-old`" in text and "not blind" in text
    # the synthetic Items 7 and 8 are under pointer_max_words: pointers, content not found
    assert summary_row(r)[9:11] == ["none", "7, 8"]
