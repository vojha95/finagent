from datetime import date
from pathlib import Path

from finagent.evidence import EvidenceStore
from finagent.evidence.integrity import read_snapshot
from finagent.filing_rows import period_disagreements, read_filing_rows
from finagent.ixbrl import tagged_table_counts
from finagent.records import RowInstance

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence"


def test_read_filing_rows_locates_with_the_tagged_cell_map():
    store = EvidenceStore(EVIDENCE_DIR)
    record = store.load_filings("AAPL", date(2026, 12, 31))[0]
    document = read_snapshot(EVIDENCE_DIR, record.primary_document, store.manifest)
    filing = read_filing_rows(document, record.accession)
    assert filing.tagged_cells == tagged_table_counts(document, filing.clean, filing.facts)
    assert set(filing.rows) == {"IS", "BS", "CF"}
    # Every located table holds tagged cells.
    assert all(filing.tagged_cells.get(r.table_index, 0) > 0
               for rows in filing.rows.values() for r in rows)


def _row(periods, kinds=None):
    return RowInstance(
        row_id="a:BS:0", accession="a", statement="BS", label="Cash", parent_header=None,
        indent=0, is_total=False, is_bold=False, position=0, cell_texts=("1",) * len(periods),
        column_periods=periods, unit_text=None, table_index=0, source_row=0,
        column_spans=tuple((i, i) for i in range(len(periods))),
        column_kinds=kinds or ("amount",) * len(periods),
    )


def test_period_disagreements():
    fiscal = {2025: date(2025, 12, 28), 2024: date(2024, 12, 29)}
    assert period_disagreements([_row((date(2025, 12, 28), date(2024, 12, 29)))], fiscal) == []
    assert period_disagreements([_row((date(2025, 12, 29), None))], fiscal) == [
        ("a:BS:0", date(2025, 12, 29), date(2025, 12, 28))]
    assert period_disagreements([_row((date(2026, 2, 9),))], fiscal) == [
        ("a:BS:0", date(2026, 2, 9), date(2025, 12, 28))]  # a borrowed report date
    assert period_disagreements([_row((date(2020, 6, 30),))], fiscal) == []  # no nearby year
    assert period_disagreements([_row((None,), ("percent",))], fiscal) == []
    assert period_disagreements([_row((date(2025, 12, 29),))], {}) == []
